"""The decisions import makes, none of which need a Paperless to test.

Every one of these is a question about what to do with an answer, and the point
of keeping them pure is that the answers can be written down. The task shapes
here are not invented: they are what `TasksViewSerializer` exposes at the pinned
`[paperless] version`, which is why `related_document` appears at all.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from pless import docscan, paperless


def _entry(name: str, sha: str, size: int = 1024) -> docscan.FileEntry:
    return docscan.FileEntry(path=Path("/documents") / name, size=size, sha256=sha)


def _scan(*supported: docscan.FileEntry, **kwargs: list[docscan.FileEntry]) -> docscan.ScanResult:
    return docscan.ScanResult(root=Path("/documents"), supported=list(supported), **kwargs)


class TestWhatATaskSays:
    """The outcome always comes from Paperless. Inferring it is the one thing
    this module must never do."""

    def test_success_is_in_the_archive(self) -> None:
        assert paperless.classify_task({"status": "SUCCESS", "result": "ok"}) == (
            paperless.Outcome.SUCCEEDED,
            "ok",
        )

    def test_a_failure_naming_a_document_is_a_duplicate(self) -> None:
        """`related_document` is populated "when creation succeeds or duplicate
        scenarios occur", so a FAILURE that still names a document means the
        archive holds it. That is done, not a failure."""
        outcome, detail = paperless.classify_task(
            {
                "status": "FAILURE",
                "result": "Not consuming invoice.pdf: It is a duplicate of Invoice (#42)",
                "related_document": 42,
            }
        )
        assert outcome is paperless.Outcome.DUPLICATE
        assert "duplicate" in detail.lower()

    def test_a_failure_naming_nothing_is_a_failure(self) -> None:
        outcome, _ = paperless.classify_task(
            {"status": "FAILURE", "result": "unparseable", "related_document": None}
        )
        assert outcome is paperless.Outcome.FAILED

    @pytest.mark.parametrize("status", ["PENDING", "STARTED", "RETRY", "", "SOMETHING_NEW"])
    def test_anything_else_is_pending(self, status: str) -> None:
        """Including a status this code has never seen. Silence is not
        consumption, and a new Paperless release must not turn into a false
        green light."""
        outcome, _ = paperless.classify_task({"status": status})
        assert outcome is paperless.Outcome.PENDING

    def test_a_failure_with_no_result_still_explains_itself(self) -> None:
        _, detail = paperless.classify_task({"status": "FAILURE"})
        assert detail  # never an empty explanation


class TestTheManifest:
    def test_it_round_trips(self) -> None:
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(
                sha256="a" * 64,
                path="/documents/a.pdf",
                size=10,
                task_id="task-1",
                outcome=paperless.Outcome.SUCCEEDED,
                detail="done",
                first_seen_at="2026-10-04T10:00:00Z",
            )
        )
        assert paperless.Manifest.from_json(manifest.to_json()) == manifest

    def test_a_missing_file_is_an_empty_manifest(self, tmp_path: Path) -> None:
        assert paperless.load_manifest(tmp_path / "absent.json").entries == {}

    def test_malformed_json_raises_rather_than_starting_over(self, tmp_path: Path) -> None:
        """The dangerous failure. Treating a corrupt manifest as empty would
        re-upload an entire collection, so it stops instead."""
        path = tmp_path / paperless.MANIFEST_NAME
        path.write_text("{not json")
        with pytest.raises(paperless.PaperlessError) as exc:
            paperless.load_manifest(path)
        assert "re-upload" in str(exc.value)

    def test_an_unknown_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / paperless.MANIFEST_NAME
        path.write_text(json.dumps({"version": 99, "entries": {}}))
        with pytest.raises(paperless.PaperlessError) as exc:
            paperless.load_manifest(path)
        assert "99" in str(exc.value)

    def test_an_unknown_outcome_reads_as_pending(self) -> None:
        """Not as a success. A record written by a future pless must not be
        taken for done by this one."""
        text = json.dumps(
            {
                "version": paperless.MANIFEST_VERSION,
                "entries": {"abc": {"sha256": "abc", "outcome": "transcended"}},
            }
        )
        assert paperless.Manifest.from_json(text).entries["abc"].outcome is (
            paperless.Outcome.PENDING
        )

    def test_it_is_written_atomically(self, tmp_path: Path) -> None:
        path = tmp_path / paperless.MANIFEST_NAME
        manifest = paperless.Manifest()
        manifest.put(paperless.UploadRecord(sha256="a", path="/a", size=1))
        paperless.save_manifest(path, manifest)
        paperless.save_manifest(path, manifest)  # again, over itself
        assert paperless.load_manifest(path) == manifest
        # No temporary file survives a successful write.
        assert [p.name for p in tmp_path.iterdir()] == [paperless.MANIFEST_NAME]

    def test_a_duplicate_counts_as_done(self) -> None:
        record = paperless.UploadRecord(sha256="a", path="/a", size=1)
        assert not record.is_done
        assert paperless.UploadRecord(
            sha256="a", path="/a", size=1, outcome=paperless.Outcome.DUPLICATE
        ).is_done


class TestPlanningARun:
    def test_an_unknown_file_is_offered(self) -> None:
        plan = paperless.plan_import(_scan(_entry("a.pdf", "h1")), paperless.Manifest())
        assert [e.path.name for e in plan.to_upload] == ["a.pdf"]

    def test_a_finished_file_is_not_offered_again(self) -> None:
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(
                sha256="h1", path="/documents/a.pdf", size=1, outcome=paperless.Outcome.SUCCEEDED
            )
        )
        plan = paperless.plan_import(_scan(_entry("a.pdf", "h1")), manifest)
        assert plan.to_upload == []
        assert plan.already_done == 1

    def test_a_renamed_file_is_still_recognised(self) -> None:
        """The manifest is keyed by content, so moving a file does not make it
        new work."""
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(
                sha256="h1", path="/old/a.pdf", size=1, outcome=paperless.Outcome.SUCCEEDED
            )
        )
        plan = paperless.plan_import(_scan(_entry("renamed.pdf", "h1")), manifest)
        assert plan.to_upload == []
        assert plan.already_done == 1

    def test_a_pending_file_is_polled_not_re_uploaded(self) -> None:
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(sha256="h1", path="/documents/a.pdf", size=1, task_id="t1")
        )
        plan = paperless.plan_import(_scan(_entry("a.pdf", "h1")), manifest)
        assert plan.to_upload == []
        assert [r.task_id for r in plan.to_poll] == ["t1"]

    def test_a_failed_file_is_left_alone(self) -> None:
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(
                sha256="h1", path="/documents/a.pdf", size=1, outcome=paperless.Outcome.FAILED
            )
        )
        plan = paperless.plan_import(_scan(_entry("a.pdf", "h1")), manifest)
        assert plan.to_upload == []
        assert plan.to_poll == []

    def test_retry_failed_offers_it_again(self) -> None:
        manifest = paperless.Manifest()
        manifest.put(
            paperless.UploadRecord(
                sha256="h1", path="/documents/a.pdf", size=1, outcome=paperless.Outcome.FAILED
            )
        )
        plan = paperless.plan_import(_scan(_entry("a.pdf", "h1")), manifest, retry_failed=True)
        assert [e.path.name for e in plan.to_upload] == ["a.pdf"]

    def test_local_duplicates_are_uploaded_once(self) -> None:
        """`scan --hashes` already found these. Sending both would spend an
        upload and an OCR to be told what we knew."""
        plan = paperless.plan_import(
            _scan(_entry("a.pdf", "same"), _entry("copy.pdf", "same")), paperless.Manifest()
        )
        assert len(plan.to_upload) == 1
        assert len(plan.local_duplicates) == 1

    def test_unsupported_and_convertible_files_are_counted_not_dropped(self) -> None:
        plan = paperless.plan_import(
            _scan(
                _entry("a.pdf", "h1"),
                needs_conversion=[_entry("b.doc", "h2")],
                unsupported=[_entry("c.zip", "h3")],
            ),
            paperless.Manifest(),
        )
        assert len(plan.needs_conversion) == 1
        assert len(plan.unsupported) == 1
        assert plan.has_work


class TestWhereThingsGo:
    def test_the_url_comes_from_the_machines_own_name(self) -> None:
        assert paperless.base_url("archive-01") == "http://archive-01:8000"

    def test_no_tailscale_name_names_the_command_that_fixes_it(self) -> None:
        with pytest.raises(paperless.PaperlessError) as exc:
            paperless.base_url("")
        assert "pless tailscale up" in str(exc.value)

    def test_the_manifest_sits_beside_the_config(self, tmp_path: Path) -> None:
        assert paperless.manifest_path(tmp_path / "pless.toml") == (
            tmp_path / paperless.MANIFEST_NAME
        )


class TestRenderingARefusal:
    def test_it_uses_paperlesss_own_words(self) -> None:
        assert "Invalid token" in paperless.render_api_error(401, {"detail": "Invalid token"})

    def test_a_bare_401_says_where_to_get_a_token(self) -> None:
        assert "PAPERLESS_API_TOKEN" in paperless.render_api_error(401, None)

    def test_an_empty_body_still_says_something(self) -> None:
        assert paperless.render_api_error(500, None).strip()

    def test_a_long_body_is_truncated(self) -> None:
        """An HTML error page should not become the whole terminal."""
        assert len(paperless.render_api_error(502, None, body="x" * 5000)) < 400

    def test_the_token_is_not_a_parameter(self) -> None:
        """The guarantee that matters, and it is structural rather than
        behavioural: this function cannot render the token because it is never
        given it. Asserting on its output instead would only prove that
        whatever we passed in came back out — which is what rendering is.

        A pasted error message is the realistic leak, so the way to prevent it
        is to keep the secret out of the only function that produces one.
        """
        parameters = set(inspect.signature(paperless.render_api_error).parameters)
        assert parameters == {"status", "payload", "body"}

    def test_the_token_is_interpolated_in_exactly_one_place(self) -> None:
        """The token reaches exactly one string: the Authorization header. If it
        is ever interpolated into a message, an error or a record, this test
        fails and someone has to look at the line that did it."""
        source = Path(paperless.__file__).read_text()
        interpolations = [line.strip() for line in source.splitlines() if "{token}" in line]
        assert interpolations == ['headers={"Authorization": f"Token {token}"},']


class TestSummarising:
    def test_it_counts_each_outcome(self) -> None:
        manifest = paperless.Manifest()
        for index, outcome in enumerate(
            [
                paperless.Outcome.SUCCEEDED,
                paperless.Outcome.SUCCEEDED,
                paperless.Outcome.DUPLICATE,
                paperless.Outcome.FAILED,
                paperless.Outcome.PENDING,
            ]
        ):
            manifest.put(
                paperless.UploadRecord(sha256=f"h{index}", path="/a", size=1, outcome=outcome)
            )
        progress = paperless.summarise(manifest)
        assert (progress.succeeded, progress.duplicate, progress.failed, progress.pending) == (
            2,
            1,
            1,
            1,
        )
        assert progress.in_archive == 3  # a duplicate is in the archive
