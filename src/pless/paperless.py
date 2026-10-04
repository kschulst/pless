"""Getting an operator's documents into the archive, and saying where each got to.

The hard part is not the upload. Paperless accepts a file, queues it, OCRs it,
and may reject it as a duplicate or fail to parse it — minutes or hours later.
So a tool that reports the upload as success is lying about the only thing the
operator cares about.

Everything here follows from that. The **manifest** is the source of truth
rather than the process, so an import survives being killed; `run_import` offers
what is not done, polls what is pending, and stops when there is nothing to do
*right now* rather than when the import is finished. Running it again is both
"resume" and "check on it".

And every ambiguous answer resolves to "not done". An unrecognised task status is
pending. A task id Paperless has pruned is pending, not failed, because
re-uploading on that basis duplicates a document already in the archive. A
malformed manifest raises rather than being read as empty, because silently
starting over would re-upload an entire collection.

The response shapes here were read from the Paperless source at the pinned
`[paperless] version` rather than inferred: `post_document` returns a bare Celery
UUID, and the duplicate signal is the task serialiser's `related_document` field
rather than its `result` string.

Like `b2`, this module reaches outward rather than to a machine: it takes a
resolved hostname, so the HTTP layer never learns what SSH is.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pless import composegen, docscan

UPLOAD_PATH = "/api/documents/post_document/"
TASKS_PATH = "/api/tasks/"

MANIFEST_NAME = "pless-import.json"
MANIFEST_VERSION = 1

# Paperless consumes serially, so a large batch buys only a longer queue and a
# worse interruption. This bounds what being killed costs.
DEFAULT_BATCH = 4

POLL_INTERVAL_SECONDS = 10

# How long one invocation waits before leaving the rest in the manifest. It does
# not wait for the import to finish, because an import takes days.
DEFAULT_POLL_BUDGET_SECONDS = 300

# How many pages of the task list to walk when matching pending ids. Each page
# covers many documents, which is the available batching — `?task_id=` accepts
# exactly one id.
MAX_TASK_PAGES = 20


class PaperlessError(RuntimeError):
    pass


class Outcome(StrEnum):
    """What became of a file. `PENDING` is also the answer to every question
    this code cannot answer, because silence is not consumption."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass
class UploadRecord:
    """One file's journey, keyed by content rather than by name.

    `sha256` is the identity so a file the operator later moves or renames is
    still recognised as done; `path` is carried for their benefit, not as a key.
    """

    sha256: str
    path: str
    size: int
    task_id: str = ""
    outcome: Outcome = Outcome.PENDING
    detail: str = ""
    first_seen_at: str = ""

    @property
    def is_done(self) -> bool:
        """A duplicate is done. The archive holds the document, which is what
        the operator asked for — recording it as a failure would make every
        re-run look worse than the last."""
        return self.outcome in (Outcome.SUCCEEDED, Outcome.DUPLICATE)


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    entries: dict[str, UploadRecord] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PaperlessError(
                f"The import manifest is not valid JSON: {exc}. Refusing to treat it as empty — "
                "starting over would re-upload every document it already accounts for. Move it "
                "aside if you really mean to start again."
            ) from None

        version = int(data.get("version") or 0)
        if version != MANIFEST_VERSION:
            raise PaperlessError(
                f"The import manifest says version {version}, and this pless understands "
                f"{MANIFEST_VERSION}. Refusing to guess at its meaning."
            )

        entries: dict[str, UploadRecord] = {}
        for key, raw in (data.get("entries") or {}).items():
            try:
                outcome = Outcome(raw.get("outcome", Outcome.PENDING))
            except ValueError:
                outcome = Outcome.PENDING  # an unknown outcome is not a success
            entries[str(key)] = UploadRecord(
                sha256=str(raw.get("sha256") or key),
                path=str(raw.get("path") or ""),
                size=int(raw.get("size") or 0),
                task_id=str(raw.get("task_id") or ""),
                outcome=outcome,
                detail=str(raw.get("detail") or ""),
                first_seen_at=str(raw.get("first_seen_at") or ""),
            )
        return cls(version=version, entries=entries)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "entries": {
                    key: {
                        "sha256": record.sha256,
                        "path": record.path,
                        "size": record.size,
                        "task_id": record.task_id,
                        "outcome": str(record.outcome),
                        "detail": record.detail,
                        "first_seen_at": record.first_seen_at,
                    }
                    for key, record in sorted(self.entries.items())
                },
            },
            indent=2,
        )

    def record_for(self, sha256: str) -> UploadRecord | None:
        return self.entries.get(sha256)

    def put(self, record: UploadRecord) -> None:
        self.entries[record.sha256] = record


@dataclass
class ImportPlan:
    to_upload: list[docscan.FileEntry] = field(default_factory=list)
    to_poll: list[UploadRecord] = field(default_factory=list)
    local_duplicates: list[docscan.FileEntry] = field(default_factory=list)
    needs_conversion: list[docscan.FileEntry] = field(default_factory=list)
    unsupported: list[docscan.FileEntry] = field(default_factory=list)
    already_done: int = 0

    @property
    def has_work(self) -> bool:
        return bool(self.to_upload or self.to_poll)


@dataclass
class ImportProgress:
    succeeded: int = 0
    duplicate: int = 0
    failed: int = 0
    pending: int = 0

    @property
    def in_archive(self) -> int:
        return self.succeeded + self.duplicate

    @property
    def total(self) -> int:
        return self.succeeded + self.duplicate + self.failed + self.pending


class Transport(Protocol):
    """The seam. The only thing that touches the network, and the only fake.

    A non-2xx status is returned rather than raised, so the orchestration can
    interpret a refusal instead of being thrown by it.
    """

    def __call__(
        self,
        method: str,
        url: str,
        token: str,
        files: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, object]: ...


# --- Pure decisions -------------------------------------------------------


def base_url(tailscale_hostname: str) -> str:
    """Where Paperless answers, from the name the machine calls itself.

    Asked of the target rather than derived from `[host]`, which may be an
    `ssh_config` alias that means nothing to HTTP.
    """
    if not tailscale_hostname:
        raise PaperlessError(
            "The target did not report a Tailscale name, so there is no address to upload to. "
            "Paperless listens on the machine's own interface only (ADR 0004), which means "
            "import goes over Tailscale. Run `pless tailscale up` first."
        )
    return f"http://{tailscale_hostname}:{composegen.WEB_PORT}"


def manifest_path(config_file: Path | None) -> Path:
    """Beside `pless.toml`, because the manifest is about local files.

    Not on the target: ADR 0019 put the verification record there because that
    record is about the archive. This one is about the operator's disk, which
    the target knows nothing about and should not.
    """
    base = config_file.parent if config_file else Path.cwd()
    return base / MANIFEST_NAME


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        return Manifest()
    return Manifest.from_json(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: Manifest) -> None:
    """Write it atomically. A half-written manifest is worse than none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(manifest.to_json())
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def classify_task(payload: dict) -> tuple[Outcome, str]:
    """What a task says became of its file.

    Read from the serialiser at the pinned version rather than guessed. The
    duplicate signal is `related_document`, which Paperless populates "when
    creation succeeds or duplicate scenarios occur" — so a FAILURE that still
    names a document means the archive holds it. Regexing `result` ourselves
    would be repeating, worse, something Paperless already does.
    """
    status = str(payload.get("status") or "").upper()
    detail = str(payload.get("result") or "").strip()

    if status == "SUCCESS":
        return Outcome.SUCCEEDED, detail
    if status == "FAILURE":
        if payload.get("related_document"):
            return Outcome.DUPLICATE, detail or "Paperless already holds this document."
        return Outcome.FAILED, detail or "Paperless could not consume this file."
    # PENDING, STARTED, RETRY — and anything this code has never seen.
    return Outcome.PENDING, detail


def plan_import(
    scan: docscan.ScanResult,
    manifest: Manifest,
    retry_failed: bool = False,
) -> ImportPlan:
    """What a run does, decided in one place and without touching the network."""
    plan = ImportPlan(
        needs_conversion=list(scan.needs_conversion),
        unsupported=list(scan.unsupported),
    )

    # The first of each local duplicate group is offered; the rest are the same
    # bytes and would cost an upload and an OCR to be told what `scan` knew.
    duplicate_paths = {
        entry.path for entries in scan.duplicate_groups().values() for entry in entries[1:]
    }

    for entry in scan.supported:
        if entry.path in duplicate_paths:
            plan.local_duplicates.append(entry)
            continue
        record = manifest.record_for(entry.sha256 or "")
        if record is None:
            plan.to_upload.append(entry)
        elif record.is_done:
            plan.already_done += 1
        elif record.outcome is Outcome.FAILED:
            if retry_failed:
                plan.to_upload.append(entry)
        else:
            plan.to_poll.append(record)

    return plan


def summarise(manifest: Manifest) -> ImportProgress:
    progress = ImportProgress()
    for record in manifest.entries.values():
        match record.outcome:
            case Outcome.SUCCEEDED:
                progress.succeeded += 1
            case Outcome.DUPLICATE:
                progress.duplicate += 1
            case Outcome.FAILED:
                progress.failed += 1
            case _:
                progress.pending += 1
    return progress


def render_api_error(status: int, payload: object, body: str = "") -> str:
    """Turn a refusal into text without ever rendering what authorised it."""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or "").strip()
        if detail:
            return f"Paperless refused the request (HTTP {status}): {detail}"
        if payload:
            return f"Paperless refused the request (HTTP {status}): {json.dumps(payload)[:300]}"
    if isinstance(payload, str) and payload.strip():
        return f"Paperless refused the request (HTTP {status}): {payload.strip()[:300]}"
    if body.strip():
        return f"Paperless refused the request (HTTP {status}): {body.strip()[:300]}"
    if status == 401:
        return (
            "Paperless rejected the API token (HTTP 401). Create one under Django admin or "
            "the Paperless UI and put it in PAPERLESS_API_TOKEN."
        )
    return f"Paperless refused the request (HTTP {status}), with no explanation in the body."


# --- Transport ------------------------------------------------------------


def http_transport(timeout: int = 120) -> Transport:
    """The only function here that touches the network."""

    def transport(
        method: str,
        url: str,
        token: str,
        files: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, object]:
        import httpx

        try:
            response = httpx.request(
                method,
                url,
                headers={"Authorization": f"Token {token}"},
                files=files,
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            # str(exc) carries the URL but never a header, so it is safe to show.
            raise PaperlessError(f"Could not reach Paperless: {exc}") from None

        try:
            payload: object = response.json()
        except ValueError:
            payload = response.text
        return response.status_code, payload

    return transport


# --- Orchestration --------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload_file(base: str, token: str, entry: docscan.FileEntry, transport: Transport) -> str:
    """Offer one file. Returns the task id Paperless will report against."""
    with entry.path.open("rb") as handle:
        status, payload = transport(
            "POST",
            f"{base}{UPLOAD_PATH}",
            token,
            files={"document": (entry.path.name, handle, "application/octet-stream")},
        )
    if not 200 <= status < 300:
        raise PaperlessError(render_api_error(status, payload))

    # `post_document` returns `Response(async_task.id)` — a bare Celery UUID.
    task_id = payload.strip('"').strip() if isinstance(payload, str) else ""
    if not task_id:
        raise PaperlessError(
            f"Paperless accepted {entry.path.name} but returned no task id, so there is no way "
            "to find out whether it was consumed. Refusing to record it as sent."
        )
    return task_id


def poll_tasks(
    base: str, token: str, task_ids: set[str], transport: Transport
) -> dict[str, tuple[Outcome, str]]:
    """Resolve as many of these task ids as the task list can account for.

    Pages of the list rather than one request per id: `?task_id=` accepts
    exactly one, while the unfiltered list is paginated newest-first and so
    covers many documents per request. An id the pages do not reach is asked
    for individually, for a task old enough to have fallen behind them.
    """
    found: dict[str, tuple[Outcome, str]] = {}
    if not task_ids:
        return found

    url: str | None = f"{base}{TASKS_PATH}"
    pages = 0
    while url and pages < MAX_TASK_PAGES and len(found) < len(task_ids):
        status, payload = transport("GET", url, token)
        if not 200 <= status < 300:
            raise PaperlessError(render_api_error(status, payload))
        if not isinstance(payload, dict):
            break
        for task in payload.get("results") or []:
            identifier = str(task.get("task_id") or "")
            if identifier in task_ids and identifier not in found:
                found[identifier] = classify_task(task)
        url = payload.get("next") or None
        pages += 1

    for identifier in task_ids - set(found):
        status, payload = transport(
            "GET", f"{base}{TASKS_PATH}", token, params={"task_id": identifier}
        )
        if not 200 <= status < 300:
            raise PaperlessError(render_api_error(status, payload))
        results = payload.get("results") if isinstance(payload, dict) else None
        if results:
            found[identifier] = classify_task(results[0])
        else:
            # Paperless prunes its task list. Treating this as a failure would
            # re-upload a document the archive may already hold.
            found[identifier] = (
                Outcome.PENDING,
                "Paperless no longer lists this task, so its outcome is unknown.",
            )
    return found


def run_import(
    base: str,
    token: str,
    scan: docscan.ScanResult,
    manifest: Manifest,
    manifest_file: Path,
    transport: Transport,
    batch: int = DEFAULT_BATCH,
    poll_budget: int = DEFAULT_POLL_BUDGET_SECONDS,
    retry_failed: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ImportProgress:
    """Offer what is not done, poll what is pending, and leave the rest recorded.

    This does not wait for an import to finish. OCR of a large collection takes
    days, so the command stops when there is nothing to do right now and the
    manifest carries the rest to the next run.
    """
    say = progress or (lambda _message: None)
    plan = plan_import(scan, manifest, retry_failed)

    # Local duplicates share a hash with a file that is or will be offered, so
    # they need no upload — only accounting, which `put` already does by key.
    if plan.local_duplicates:
        say(
            f"{len(plan.local_duplicates)} file(s) are byte-identical to another in this "
            "collection, and are counted against it rather than uploaded again."
        )

    pending: set[str] = {record.task_id for record in plan.to_poll if record.task_id}

    for index, entry in enumerate(plan.to_upload, start=1):
        say(f"Uploading {index}/{len(plan.to_upload)}: {entry.path.name}")
        task_id = upload_file(base, token, entry, transport)
        manifest.put(
            UploadRecord(
                sha256=entry.sha256 or "",
                path=str(entry.path),
                size=entry.size,
                task_id=task_id,
                outcome=Outcome.PENDING,
                first_seen_at=_now(),
            )
        )
        # After every upload: an interruption then costs one file's knowledge.
        save_manifest(manifest_file, manifest)
        pending.add(task_id)

        if index % batch == 0:
            _collect(base, token, pending, manifest, manifest_file, transport, say)

    # Always ask once, even for an import smaller than `batch` and even with no
    # budget to wait: Paperless may well have finished already, and reporting
    # everything as pending would send the operator away for nothing.
    if pending:
        _collect(base, token, pending, manifest, manifest_file, transport, say)

    deadline = time.monotonic() + poll_budget
    while pending and time.monotonic() < deadline:
        _collect(base, token, pending, manifest, manifest_file, transport, say)
        if pending:
            time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))

    if pending:
        say(
            f"{len(pending)} document(s) are still being worked on. They are recorded; run this "
            "again to collect them."
        )
    return summarise(manifest)


def _collect(
    base: str,
    token: str,
    pending: set[str],
    manifest: Manifest,
    manifest_file: Path,
    transport: Transport,
    say: Callable[[str], None],
) -> None:
    """Resolve what has finished, and write it down."""
    resolved = poll_tasks(base, token, set(pending), transport)
    for task_id, (outcome, detail) in resolved.items():
        for record in manifest.entries.values():
            if record.task_id == task_id:
                # A pending answer still carries an explanation worth keeping —
                # "Paperless no longer lists this task" is what tells an
                # operator why an entry is stuck, and why we are not re-sending
                # it. Only the outcome is withheld until it is known.
                manifest.put(replace(record, outcome=outcome, detail=detail or record.detail))
                break
        if outcome is not Outcome.PENDING:
            pending.discard(task_id)
    save_manifest(manifest_file, manifest)
