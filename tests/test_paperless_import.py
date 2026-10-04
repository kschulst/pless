"""The sequence: upload, poll, persist, and survive being killed.

These are the behaviours that cannot be checked by looking at one function.
They need a transport that records what it was asked and answers like Paperless
would — including the awkward answers, which is where the value is. A fake that
says 200 to everything has no opinion about what happens when a task is pruned.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

from pless import docscan, paperless


class RecordingPaperless:
    """A Paperless that remembers what it was asked.

    `outcomes` maps a task id to the status it will report, so a test can say
    "this one succeeds, this one is a duplicate, this one is never heard from".
    """

    def __init__(
        self,
        outcomes: dict[str, dict] | None = None,
        fail_upload_after: int | None = None,
        known_tasks: set[str] | None = None,
    ) -> None:
        self.uploads: list[str] = []
        self.task_queries: list[str] = []
        self.page_requests = 0
        self.outcomes = outcomes or {}
        self.fail_upload_after = fail_upload_after
        self.known_tasks = known_tasks
        self._issued = 0

    def __call__(
        self,
        method: str,
        url: str,
        token: str,
        files: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, object]:
        if method == "POST":
            if self.fail_upload_after is not None and len(self.uploads) >= self.fail_upload_after:
                raise KeyboardInterrupt  # the operator gives up mid-import
            name = files["document"][0] if files else "?"
            self.uploads.append(name)
            self._issued += 1
            return 200, f"task-{self._issued}"

        if params and "task_id" in params:
            self.task_queries.append(params["task_id"])
            task = self._task(params["task_id"])
            return 200, {"results": [task] if task else [], "next": None}

        self.page_requests += 1
        tasks = [t for t in (self._task(i) for i in self.outcomes) if t]
        return 200, {"results": tasks, "next": None}

    def _task(self, task_id: str) -> dict | None:
        if self.known_tasks is not None and task_id not in self.known_tasks:
            return None  # Paperless has pruned it
        if task_id not in self.outcomes:
            return None
        return {"task_id": task_id, **self.outcomes[task_id]}


SUCCESS = {"status": "SUCCESS", "result": "Success. New document id 1 created"}
DUPLICATE = {"status": "FAILURE", "result": "It is a duplicate", "related_document": 7}
FAILURE = {"status": "FAILURE", "result": "Unsupported", "related_document": None}
STILL_GOING = {"status": "STARTED", "result": ""}


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    root = tmp_path / "documents"
    root.mkdir()
    for name, content in [("a.pdf", b"aaa"), ("b.pdf", b"bbb"), ("c.pdf", b"ccc")]:
        (root / name).write_bytes(content)
    return root


def _scan(root: Path) -> docscan.ScanResult:
    return docscan.scan(root, with_hashes=True)


def _run(
    transport: RecordingPaperless,
    scan: docscan.ScanResult,
    manifest_file: Path,
    manifest: paperless.Manifest | None = None,
    **kwargs: object,
) -> paperless.ImportProgress:
    return paperless.run_import(
        "http://archive:8000",
        "token",
        scan,
        manifest if manifest is not None else paperless.Manifest(),
        manifest_file,
        transport,
        poll_budget=0,  # no sleeping in tests; one collection pass
        **kwargs,
    )


class TestAFirstRun:
    def test_every_file_is_offered(self, collection: Path, tmp_path: Path) -> None:
        transport = RecordingPaperless()
        _run(transport, _scan(collection), tmp_path / "m.json")
        assert sorted(transport.uploads) == ["a.pdf", "b.pdf", "c.pdf"]

    def test_outcomes_are_recorded_against_each_file(
        self, collection: Path, tmp_path: Path
    ) -> None:
        transport = RecordingPaperless(
            outcomes={"task-1": SUCCESS, "task-2": DUPLICATE, "task-3": FAILURE}
        )
        progress = _run(transport, _scan(collection), tmp_path / "m.json")
        assert (progress.succeeded, progress.duplicate, progress.failed) == (1, 1, 1)

    def test_uploading_is_not_reported_as_consuming(self, collection: Path, tmp_path: Path) -> None:
        """Three files uploaded, none consumed yet. The summary must not
        pretend otherwise — this is the lie the whole design exists to avoid."""
        transport = RecordingPaperless(
            outcomes={"task-1": STILL_GOING, "task-2": STILL_GOING, "task-3": STILL_GOING}
        )
        progress = _run(transport, _scan(collection), tmp_path / "m.json")
        assert len(transport.uploads) == 3
        assert progress.in_archive == 0
        assert progress.pending == 3


class TestBeingInterrupted:
    def test_what_was_uploaded_is_on_disk(self, collection: Path, tmp_path: Path) -> None:
        """The manifest is written after every upload, so being killed costs at
        most the one in flight."""
        manifest_file = tmp_path / "m.json"
        transport = RecordingPaperless(fail_upload_after=2)
        with pytest.raises(KeyboardInterrupt):
            _run(transport, _scan(collection), manifest_file)

        recovered = paperless.load_manifest(manifest_file)
        assert len(recovered.entries) == 2
        assert all(r.task_id for r in recovered.entries.values())

    def test_a_second_run_offers_only_what_is_left(self, collection: Path, tmp_path: Path) -> None:
        manifest_file = tmp_path / "m.json"
        with pytest.raises(KeyboardInterrupt):
            _run(RecordingPaperless(fail_upload_after=2), _scan(collection), manifest_file)

        resumed = RecordingPaperless(outcomes={"task-1": SUCCESS})
        _run(resumed, _scan(collection), manifest_file, paperless.load_manifest(manifest_file))
        assert len(resumed.uploads) == 1  # the third file, and nothing re-sent

    def test_a_third_run_uploads_nothing(self, collection: Path, tmp_path: Path) -> None:
        manifest_file = tmp_path / "m.json"
        outcomes = {"task-1": SUCCESS, "task-2": SUCCESS, "task-3": SUCCESS}
        _run(RecordingPaperless(outcomes=outcomes), _scan(collection), manifest_file)

        again = RecordingPaperless(outcomes=outcomes)
        progress = _run(
            again, _scan(collection), manifest_file, paperless.load_manifest(manifest_file)
        )
        assert again.uploads == []
        assert progress.in_archive == 3


class TestCollectingLater:
    def test_a_pending_task_is_resolved_on_a_later_run(
        self, collection: Path, tmp_path: Path
    ) -> None:
        """The run that uploads need not be the run that learns the outcome.
        This is what makes "run it again" a complete answer to both resuming and
        checking on progress."""
        manifest_file = tmp_path / "m.json"
        pending = {"task-1": STILL_GOING, "task-2": STILL_GOING, "task-3": STILL_GOING}
        first = _run(RecordingPaperless(outcomes=pending), _scan(collection), manifest_file)
        assert first.pending == 3

        done = {"task-1": SUCCESS, "task-2": SUCCESS, "task-3": DUPLICATE}
        later = RecordingPaperless(outcomes=done)
        second = _run(
            later, _scan(collection), manifest_file, paperless.load_manifest(manifest_file)
        )
        assert later.uploads == []  # nothing re-uploaded to find out
        assert second.in_archive == 3
        assert second.pending == 0

    def test_a_pruned_task_stays_pending(self, collection: Path, tmp_path: Path) -> None:
        """The dangerous case. Paperless prunes its task list; calling that a
        failure would re-upload a document the archive may already hold, and
        duplicate it."""
        manifest_file = tmp_path / "m.json"
        transport = RecordingPaperless(outcomes={}, known_tasks=set())
        progress = _run(transport, _scan(collection), manifest_file)

        assert progress.failed == 0
        assert progress.pending == 3
        record = next(iter(paperless.load_manifest(manifest_file).entries.values()))
        assert "no longer lists" in record.detail

    def test_the_task_list_is_read_in_pages_not_one_request_each(
        self, collection: Path, tmp_path: Path
    ) -> None:
        """`?task_id=` takes one id, so polling reads the list instead. Three
        documents must not mean three task requests."""
        transport = RecordingPaperless(
            outcomes={"task-1": SUCCESS, "task-2": SUCCESS, "task-3": SUCCESS}
        )
        _run(transport, _scan(collection), tmp_path / "m.json", batch=10)
        assert transport.task_queries == []  # all three found in one page
        assert transport.page_requests == 1

    def test_a_task_behind_the_pages_is_asked_for_directly(
        self, collection: Path, tmp_path: Path
    ) -> None:
        """The fallback: a task too old for the pages but not yet pruned."""
        transport = RecordingPaperless(
            outcomes={"task-1": SUCCESS, "task-2": SUCCESS, "task-3": SUCCESS},
            known_tasks={"task-1", "task-2"},
        )
        _run(transport, _scan(collection), tmp_path / "m.json", batch=10)
        assert transport.task_queries == ["task-3"]


class TestLocalDuplicates:
    def test_identical_files_are_uploaded_once(self, tmp_path: Path) -> None:
        root = tmp_path / "documents"
        root.mkdir()
        (root / "invoice.pdf").write_bytes(b"identical")
        (root / "invoice-copy.pdf").write_bytes(b"identical")

        transport = RecordingPaperless(outcomes={"task-1": SUCCESS})
        progress = _run(transport, _scan(root), tmp_path / "m.json")
        assert len(transport.uploads) == 1
        assert progress.in_archive == 1


class TestDryRun:
    def test_it_makes_no_request(self, collection: Path) -> None:
        """`plan_import` is pure, which is what lets --dry-run be a promise
        rather than a careful implementation."""
        plan = paperless.plan_import(_scan(collection), paperless.Manifest())
        assert len(plan.to_upload) == 3


class TestUploadRefusals:
    def test_a_refusal_becomes_a_paperless_error(self, collection: Path, tmp_path: Path) -> None:
        def refuse(method, url, token, files=None, params=None):
            return 401, {"detail": "Invalid token."}

        with pytest.raises(paperless.PaperlessError) as exc:
            _run(refuse, _scan(collection), tmp_path / "m.json")
        assert "Invalid token." in str(exc.value)

    def test_an_accepted_file_with_no_task_id_is_refused(
        self, collection: Path, tmp_path: Path
    ) -> None:
        """A file whose task is unknown can never be followed up, so recording
        it as sent would be recording a thing we cannot check."""

        def no_task_id(method, url, token, files=None, params=None):
            return 200, ""

        with pytest.raises(paperless.PaperlessError) as exc:
            _run(no_task_id, _scan(collection), tmp_path / "m.json")
        assert "no task id" in str(exc.value)


def _parameters(func: Callable[..., object]) -> list[str]:
    return [p.name for p in inspect.signature(func).parameters.values()]


class TestTheFakes:
    """A fake whose signature has drifted is how these tests keep passing while
    the command breaks."""

    def test_the_recording_transport_matches_the_protocol(self) -> None:
        assert (
            _parameters(RecordingPaperless.__call__)[1:]
            == _parameters(paperless.Transport.__call__)[1:]
        )

    def test_it_satisfies_the_protocol_at_runtime(self) -> None:
        transport: paperless.Transport = RecordingPaperless()
        status, payload = transport(
            "POST", "http://x/", "t", files={"document": ("a.pdf", b"", "")}
        )
        assert status == 200 and payload == "task-1"
