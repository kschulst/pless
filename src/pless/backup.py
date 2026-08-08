"""Backing up the archive to an encrypted restic repository.

The pipeline runs on the target, because the operator's laptop is asleep,
travelling or reinstalled too often to be part of a backup schedule. It is
written once, as a shell script rendered from configuration, and both the
systemd timer and `pless backup run` invoke that same script — a second
implementation in Python would drift from it within weeks.

What lives here in Python is everything that can be decided without a machine:
rendering the script, the units and the environment file; parsing what restic
and Paperless say; and reading the record a run leaves behind. That split is
what lets the interesting parts be tested without a target
(ADR 0009), and it is why this module imports neither typer nor rich.

Staging happens on the encrypted volume, so the export and the database dump
are protected at rest without any extra work.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pless import composegen, config, sshexec, storage
from pless.targets import Host

STAGING_DIR = f"{composegen.INSTALL_DIR}/backups"
EXPORT_DIR = f"{composegen.INSTALL_DIR}/export"
ENV_FILE = f"{composegen.INSTALL_DIR}/backup.env"
RUN_RECORD = f"{STAGING_DIR}/last-run.json"
VERIFY_RECORD = f"{STAGING_DIR}/verification.json"

# The script lives on the root filesystem, not on the encrypted volume. It has
# to be able to run *while the volume is locked* in order to report a calm skip;
# a script that is itself unreachable makes the unit fail after every reboot,
# which is the alarm fatigue this design exists to avoid. The environment file
# stays on the encrypted volume, because that one holds a secret, and the script
# only sources it after confirming the volume is mounted.
SCRIPT_DIR = "/usr/local/lib/pless"
SCRIPT_PATH = f"{SCRIPT_DIR}/pless-backup.sh"
VERIFY_SCRIPT_PATH = f"{SCRIPT_DIR}/pless-backup-verify.sh"

SERVICE_UNIT = "/etc/systemd/system/pless-backup.service"
TIMER_UNIT = "/etc/systemd/system/pless-backup.timer"
VERIFY_SERVICE_UNIT = "/etc/systemd/system/pless-backup-verify.service"
VERIFY_TIMER_UNIT = "/etc/systemd/system/pless-backup-verify.timer"

SNAPSHOT_TAG = "pless"

# A locked volume is the normal state after a reboot, not a failure. The script
# exits with this, and the unit treats it as success, so a routine skip never
# trains the operator to ignore backup alerts.
EXIT_SKIPPED = 75


class BackupError(RuntimeError):
    pass


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_LOCKED = "skipped-locked"


@dataclass
class QueueState:
    """What Paperless is currently working on.

    Paperless has no pause API — the documentation only says that nothing must
    be consuming while the exporter runs. Since import goes through the REST
    API (ADR 0012), the task queue is the real signal.
    """

    pending: int | None
    active: int | None

    @property
    def is_drained(self) -> bool:
        # Unknown is never treated as drained: the exporter needs a stable set
        # of documents, and guessing would produce a quietly wrong backup.
        return self.pending == 0 and self.active == 0


@dataclass
class BackupRun:
    started_at: str = ""
    finished_at: str = ""
    outcome: RunOutcome = RunOutcome.FAILED
    snapshot_id: str = ""
    documents_exported: int = 0
    queue_moved_during_run: bool = False
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome is RunOutcome.SUCCEEDED

    @classmethod
    def from_json(cls, text: str) -> BackupRun:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BackupError(
                f"{RUN_RECORD} on the target is not valid JSON: {exc}. "
                "Delete it and run `pless backup run` to write a fresh one."
            ) from exc
        try:
            outcome = RunOutcome(data.get("outcome", RunOutcome.FAILED))
        except ValueError:
            outcome = RunOutcome.FAILED
        return cls(
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            outcome=outcome,
            snapshot_id=str(data.get("snapshot_id", "")),
            documents_exported=int(data.get("documents_exported", 0) or 0),
            queue_moved_during_run=bool(data.get("queue_moved_during_run", False)),
            detail=str(data.get("detail", "")),
        )


class VerificationLevel(StrEnum):
    # The same restore path at two depths. Content is cheap enough to run on a
    # timer; the full rehearsal is the only one that also tests the procedure.
    CONTENT = "content"
    FULL = "full"


@dataclass
class VerificationRecord:
    """Evidence that a restore actually produced the documents.

    Lives on the target beside the data it describes, and names a snapshot, so
    the claim can be checked against the repository rather than taken on trust
    (ADR 0019). `preflight` reads it across process boundaries, which is why it
    is persisted at all.
    """

    level: VerificationLevel = VerificationLevel.CONTENT
    performed_at: str = ""
    snapshot_id: str = ""
    repository_kind: str = ""
    passed: bool = False
    documents_expected: int = 0
    documents_found: int = 0
    sample_size: int = 0
    mismatches: list[str] = field(default_factory=list)
    detail: str = ""

    def age_days(self, now: datetime) -> float | None:
        """How old the record is, or None if it carries no usable timestamp."""
        if not self.performed_at:
            return None
        try:
            performed = datetime.fromisoformat(self.performed_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if performed.tzinfo is None:
            performed = performed.replace(tzinfo=UTC)
        return (now - performed).total_seconds() / 86400

    def is_stale(self, max_age_days: int, now: datetime) -> bool:
        """A restore proved eleven months ago proves little about today.

        An unreadable or missing timestamp counts as stale: the point of the
        record is to answer "recently?", and a record that cannot say when is
        not evidence.
        """
        age = self.age_days(now)
        if age is None:
            return True
        return age > max_age_days

    @classmethod
    def from_json(cls, text: str) -> VerificationRecord:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BackupError(
                f"{VERIFY_RECORD} on the target is not valid JSON: {exc}. "
                "Run `pless backup verify` to write a fresh one."
            ) from exc
        try:
            level = VerificationLevel(data.get("level", VerificationLevel.CONTENT))
        except ValueError:
            level = VerificationLevel.CONTENT
        return cls(
            level=level,
            performed_at=str(data.get("performed_at", "")),
            snapshot_id=str(data.get("snapshot_id", "")),
            repository_kind=str(data.get("repository_kind", "")),
            passed=bool(data.get("passed", False)),
            documents_expected=int(data.get("documents_expected", 0) or 0),
            documents_found=int(data.get("documents_found", 0) or 0),
            sample_size=int(data.get("sample_size", 0) or 0),
            mismatches=[str(m) for m in (data.get("mismatches") or [])],
            detail=str(data.get("detail", "")),
        )


@dataclass
class Snapshot:
    id: str
    time: str
    hostname: str = ""
    tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.id[:8]


@dataclass
class RetentionPolicy:
    daily: int
    weekly: int
    monthly: int
    yearly: int

    @classmethod
    def from_config(cls, cfg: config.Config) -> RetentionPolicy:
        return cls(
            daily=cfg.backup.retention_daily,
            weekly=cfg.backup.retention_weekly,
            monthly=cfg.backup.retention_monthly,
            yearly=cfg.backup.retention_yearly,
        )

    def forget_args(self) -> list[str]:
        return [
            "--keep-daily",
            str(self.daily),
            "--keep-weekly",
            str(self.weekly),
            "--keep-monthly",
            str(self.monthly),
            "--keep-yearly",
            str(self.yearly),
            "--tag",
            SNAPSHOT_TAG,
        ]


# --- Rendering ------------------------------------------------------------
#
# Everything below is a pure function of configuration: no timestamps, no
# host-specific values, nothing random. That makes the units snapshot-testable
# and makes writing them twice a no-op.


def _shell_quote(value: str) -> str:
    """Quote for a file that will be sourced by /bin/sh."""
    return "'" + value.replace("'", "'\\''") + "'"


def render_backup_env(cfg: config.Config, secrets: config.Secrets) -> str:
    """The environment the script sources: repository and credentials.

    Written to the target with `tee` at mode 0600, on the encrypted volume, so
    it is unreadable whenever the volume is locked. Never interpolated into a
    command string (ADR 0014).
    """
    missing: list[str] = []
    if not cfg.backup.restic_repository:
        missing.append("[backup] restic_repository in pless.toml")
    if not secrets.restic_password:
        missing.append("RESTIC_PASSWORD in .env")
    # Both kinds need the same pair of object-storage credentials; only the
    # variable names restic reads differ. A B2 application key doubles as an
    # S3 credential, which is what makes the S3 endpoint usable at all.
    if cfg.backup.repository_kind in ("b2", "s3"):
        if not secrets.b2_key_id:
            missing.append("B2_KEY_ID in .env (the keyID)")
        if not secrets.b2_application_key:
            missing.append("B2_APPLICATION_KEY in .env (the applicationKey)")
    if missing:
        raise BackupError(
            "Backup is not configured yet. Missing: "
            + "; ".join(missing)
            + ". Generate a passphrase with `pless secrets generate` and save it in your "
            "password manager first — RESTIC_PASSWORD cannot be recovered."
        )

    lines = [
        "# Written by `pless backup init`. Sourced by pless-backup.sh.",
        f"RESTIC_REPOSITORY={_shell_quote(cfg.backup.restic_repository)}",
        f"RESTIC_PASSWORD={_shell_quote(secrets.restic_password)}",
    ]
    # This is the translation boundary. Our configuration uses the names B2 puts
    # on screen — keyID and applicationKey — because that is what the operator
    # is copying from. restic wants its own names, and different ones per
    # backend, from the very same credential. Neither side has to know about
    # the other's vocabulary.
    if cfg.backup.repository_kind == "b2":
        lines.append(f"B2_ACCOUNT_ID={_shell_quote(secrets.b2_key_id)}")
        lines.append(f"B2_ACCOUNT_KEY={_shell_quote(secrets.b2_application_key)}")
    elif cfg.backup.repository_kind == "s3":
        lines.append(f"AWS_ACCESS_KEY_ID={_shell_quote(secrets.b2_key_id)}")
        lines.append(f"AWS_SECRET_ACCESS_KEY={_shell_quote(secrets.b2_application_key)}")
    lines.append("export RESTIC_REPOSITORY RESTIC_PASSWORD")
    if cfg.backup.repository_kind == "b2":
        lines.append("export B2_ACCOUNT_ID B2_ACCOUNT_KEY")
    elif cfg.backup.repository_kind == "s3":
        lines.append("export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY")
    return "\n".join(lines) + "\n"


def render_script(cfg: config.Config) -> str:
    """The pipeline, as the one implementation both systemd and pless invoke.

    Order matters. The dump comes first so the database is the older of the two
    artefacts: a document consumed between the two steps then appears in the
    export without a row in the dump, which a restore can ignore. The reverse
    would produce a row pointing at a file nobody exported.
    """
    delete_flag = " --delete" if cfg.backup.exporter_delete else ""
    return f"""\
#!/bin/sh
# Rendered by pless. Do not edit here — `pless backup init` overwrites it.
set -eu

INSTALL_DIR={composegen.INSTALL_DIR}
STAGING={STAGING_DIR}
EXPORT_DIR={EXPORT_DIR}
RECORD={RUN_RECORD}
EXIT_SKIPPED={EXIT_SKIPPED}
QUIESCENCE_TIMEOUT={cfg.backup.quiescence_timeout_seconds}
POLL_SECONDS=10

STEP=starting
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SNAPSHOT_ID=""
DOCUMENTS=0
QUEUE_MOVED=false

log() {{ echo "pless-backup: $*" >&2; }}

write_record() {{
  # $1 outcome, $2 detail
  mkdir -p "$STAGING"
  cat > "$RECORD" <<RECORD_EOF
{{
  "started_at": "$STARTED_AT",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "outcome": "$1",
  "snapshot_id": "$SNAPSHOT_ID",
  "documents_exported": $DOCUMENTS,
  "queue_moved_during_run": $QUEUE_MOVED,
  "detail": "$2"
}}
RECORD_EOF
  chmod 0600 "$RECORD"
}}

on_exit() {{
  code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne "$EXIT_SKIPPED" ]; then
    write_record failed "Failed during: $STEP"
  fi
}}
trap on_exit EXIT

# A locked volume is normal after every reboot. Say so and stop, rather than
# failing in a way that teaches the operator to ignore backup alerts.
if ! mountpoint -q "$INSTALL_DIR"; then
  log "the encrypted volume is not mounted — nothing to back up. Run \\`pless unlock\\`."
  exit "$EXIT_SKIPPED"
fi

. {ENV_FILE}
cd "$INSTALL_DIR"
mkdir -p "$STAGING"

# Written as failed before any work happens, so a run that is interrupted —
# power cut, timeout, OOM — reads as failed rather than as never having run.
write_record failed "Run in progress, or interrupted before it finished."

queue_pending() {{
  docker compose exec -T broker redis-cli -n 0 LLEN celery 2>/dev/null | tr -d '\\r' || echo unknown
}}

queue_active() {{
  # `inspect active` prints "- empty -" when nothing is running.
  docker compose exec -T webserver celery -A paperless inspect active 2>/dev/null || echo unknown
}}

STEP="waiting for the task queue to drain"
log "$STEP (timeout ${{QUIESCENCE_TIMEOUT}}s)"
DRAINED=0
WAITED=0
while [ "$WAITED" -lt "$QUIESCENCE_TIMEOUT" ]; do
  PENDING=$(queue_pending)
  ACTIVE=$(queue_active)
  if [ "$PENDING" = "0" ] && echo "$ACTIVE" | grep -q -- "- empty -"; then
    DRAINED=$((DRAINED + 1))
    # Twice in a row: one empty poll can fall between two tasks.
    [ "$DRAINED" -ge 2 ] && break
  else
    DRAINED=0
  fi
  sleep "$POLL_SECONDS"
  WAITED=$((WAITED + POLL_SECONDS))
done

if [ "$DRAINED" -lt 2 ]; then
  log "the queue did not drain within ${{QUIESCENCE_TIMEOUT}}s — not exporting."
  write_record failed "The task queue did not drain within ${{QUIESCENCE_TIMEOUT}}s. \
The exporter requires that nothing is being consumed, so nothing was exported."
  exit 1
fi

STEP="dumping the database"
log "$STEP"
# Authenticate explicitly rather than leaning on the image's default, which
# trusts local socket connections. -h forces TCP, so the password is actually
# checked; PGPASSWORD comes from the container's own environment, so the secret
# never appears in argv on either machine (ADR 0014).
docker compose exec -T db sh -c \\
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U paperless -d paperless -Fc' \\
  > "$STAGING/db.dump.new"
mv "$STAGING/db.dump.new" "$STAGING/db.dump"

STEP="exporting documents"
log "$STEP"
docker compose exec -T webserver document_exporter /usr/src/paperless/export \\
  --no-progress-bar --split-manifest{delete_flag}

# --split-manifest writes "<document stem>-manifest.json" beside each document,
# so this counts documents rather than files, whatever the filename format is
# and whether or not an archive copy exists alongside the original. Matching
# what we want beats excluding the top-level files we know about today, which
# would miscount the moment Paperless adds another one.
DOCUMENTS=$(find "$EXPORT_DIR" -type f -name '*-manifest.json' | wc -l | tr -d ' ')

STEP="re-checking the queue"
PENDING=$(queue_pending)
ACTIVE=$(queue_active)
if [ "$PENDING" != "0" ] || ! echo "$ACTIVE" | grep -q -- "- empty -"; then
  QUEUE_MOVED=true
  log "work arrived while the export ran — the snapshot is still taken, and says so."
fi

STEP="writing the snapshot"
log "$STEP"
restic backup --tag {SNAPSHOT_TAG} --json "$EXPORT_DIR" "$STAGING/db.dump" \\
  > "$STAGING/restic-backup.json"
SNAPSHOT_ID=$(grep -o '"snapshot_id":"[^"]*"' "$STAGING/restic-backup.json" \\
  | tail -1 | cut -d'"' -f4)

write_record succeeded "Exported $DOCUMENTS documents into snapshot ${{SNAPSHOT_ID}}."
log "done: $DOCUMENTS documents, snapshot $SNAPSHOT_ID"
"""


def render_verify_script(cfg: config.Config) -> str:
    """Content verification: restore a sample and prove the documents come back.

    Deliberately not a hash comparison against Paperless's own manifest. restic
    verifies content hashes as it restores, so a restore that succeeds *is* the
    content proof, and `restic ls` gives the document count without moving any
    data at all. Cost therefore scales with the sample rather than the archive,
    and nothing here depends on Paperless's manifest internals.

    The count is compared against what the run recorded, not against the live
    archive: the natural time to verify is right after a backup, when the source
    has legitimately moved on. Drift against the live count is information, not
    failure.
    """
    return f"""\
#!/bin/sh
# Rendered by pless. Do not edit here — `pless backup init` overwrites it.
set -eu

INSTALL_DIR={composegen.INSTALL_DIR}
STAGING={STAGING_DIR}
RECORD={VERIFY_RECORD}
RUN_RECORD={RUN_RECORD}
EXIT_SKIPPED={EXIT_SKIPPED}
SAMPLE_SIZE={cfg.backup.verify_sample_size}
REPOSITORY_KIND={cfg.backup.repository_kind}

PERFORMED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SNAPSHOT_ID=""
EXPECTED=0
FOUND=0
SAMPLED=0
PASSED=false
DETAIL=""

log() {{ echo "pless-backup-verify: $*" >&2; }}

write_record() {{
  mkdir -p "$STAGING"
  cat > "$RECORD" <<RECORD_EOF
{{
  "level": "content",
  "performed_at": "$PERFORMED_AT",
  "snapshot_id": "$SNAPSHOT_ID",
  "repository_kind": "$REPOSITORY_KIND",
  "passed": $PASSED,
  "documents_expected": $EXPECTED,
  "documents_found": $FOUND,
  "sample_size": $SAMPLED,
  "mismatches": [],
  "detail": "$1"
}}
RECORD_EOF
  chmod 0600 "$RECORD"
}}

on_exit() {{
  code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne "$EXIT_SKIPPED" ]; then
    write_record "Verification failed: $DETAIL"
  fi
}}
trap on_exit EXIT

if ! mountpoint -q "$INSTALL_DIR"; then
  log "the encrypted volume is not mounted — cannot verify. Run \\`pless unlock\\`."
  exit "$EXIT_SKIPPED"
fi

. {ENV_FILE}
SCRATCH=$(mktemp -d "$STAGING/verify-XXXXXX")
cleanup_scratch() {{ rm -rf "$SCRATCH"; }}
trap 'cleanup_scratch; on_exit' EXIT

DETAIL="could not read the snapshot list"
SNAPSHOT_ID=$(restic snapshots --tag {SNAPSHOT_TAG} --json 2>/dev/null \\
  | tr ',' '\\n' | grep -o '"id":"[^"]*"' | tail -1 | cut -d'"' -f4 || true)

# An empty repository is a failure, not "nothing yet". A repository that was
# silently recreated looks exactly like success and contains nothing.
if [ -z "$SNAPSHOT_ID" ]; then
  DETAIL="No snapshots tagged {SNAPSHOT_TAG} in the repository. Either nothing has been \
backed up yet, or the repository was recreated — the second looks like success and holds nothing."
  log "$DETAIL"
  write_record "$DETAIL"
  exit 1
fi

# Counting from the snapshot listing costs nothing: no data is moved.
DETAIL="could not list the snapshot contents"
LISTING=$(restic ls "$SNAPSHOT_ID")
FOUND=$(echo "$LISTING" | grep -c -- '-manifest\\.json$' || true)
EXPECTED=$(grep -o '"documents_exported"[^0-9]*[0-9]*' "$RUN_RECORD" 2>/dev/null \\
  | grep -o '[0-9]*$' || echo 0)
[ -n "$EXPECTED" ] || EXPECTED=0

# Restore a sample rather than the archive. restic verifies content hashes as
# it restores, so a sample that comes back is a sample proven intact.
SAMPLE=$(echo "$LISTING" | grep "$INSTALL_DIR/export/" | grep -v -- '-manifest\\.json$' \\
  | grep -v -- '-thumbnail\\.webp$' | shuf -n "$SAMPLE_SIZE" || true)
SAMPLED=$(echo "$SAMPLE" | grep -c . || true)

DETAIL="the sample could not be restored"
if [ "$SAMPLED" -gt 0 ]; then
  # Paperless filenames contain spaces — "2026-08-08 Some Title.pdf" is the
  # normal case, not an edge case. Building these into a string and expanding
  # it unquoted splits every filename in half, and restic reads the fragments
  # as extra snapshot IDs. Positional parameters carry the spaces intact.
  set --
  while IFS= read -r sample_path; do
    [ -n "$sample_path" ] || continue
    set -- "$@" --include "$sample_path"
  done <<SAMPLE_EOF
$SAMPLE
SAMPLE_EOF
  restic restore "$SNAPSHOT_ID" --target "$SCRATCH" "$@" > /dev/null
  RESTORED=$(find "$SCRATCH" -type f | wc -l | tr -d ' ')
else
  RESTORED=0
fi

if [ "$FOUND" -eq 0 ]; then
  DETAIL="The snapshot contains no documents at all."
elif [ "$EXPECTED" -gt 0 ] && [ "$FOUND" -ne "$EXPECTED" ]; then
  DETAIL="The snapshot holds $FOUND documents; the run that produced it recorded $EXPECTED."
elif [ "$SAMPLED" -gt 0 ] && [ "$RESTORED" -lt "$SAMPLED" ]; then
  DETAIL="Restored only $RESTORED of $SAMPLED sampled files."
else
  PASSED=true
  DETAIL="Restored $RESTORED of $SAMPLED sampled files from snapshot $SNAPSHOT_ID; \
$FOUND documents present, matching the run that produced it."
fi

write_record "$DETAIL"
cleanup_scratch
trap - EXIT
log "$DETAIL"
[ "$PASSED" = true ] || exit 1
"""


def render_units(cfg: config.Config) -> dict[str, str]:
    """Unit and timer, keyed by their absolute path on the target."""
    # Deliberately no RequiresMountsFor: it would turn "the volume is locked"
    # into a dependency failure, which is the alarm we are trying not to raise.
    # The script checks the mount itself and exits EXIT_SKIPPED, which systemd
    # is told to treat as success.
    service = f"""\
[Unit]
Description=pless backup (export, database dump, restic snapshot)
After=paperless.service

[Service]
Type=oneshot
ExecStart=/bin/sh {SCRIPT_PATH}
# A locked volume is a skip, not a failure. The volume is locked after every
# reboot, so this is a routine outcome and must not mark the unit failed.
SuccessExitStatus={EXIT_SKIPPED}
"""
    timer = f"""\
[Unit]
Description=pless backup schedule

[Timer]
OnCalendar={cfg.backup.schedule}
# A machine that was asleep at the scheduled time still backs up when it wakes.
Persistent=true
RandomizedDelaySec=900

[Install]
WantedBy=timers.target
"""
    verify_service = f"""\
[Unit]
Description=pless backup verification (restore a sample and prove it comes back)
After=paperless.service

[Service]
Type=oneshot
ExecStart=/bin/sh {VERIFY_SCRIPT_PATH}
# A locked volume is a skip, not a failure.
SuccessExitStatus={EXIT_SKIPPED}
"""
    verify_timer = f"""\
[Unit]
Description=pless backup verification schedule

[Timer]
OnCalendar={cfg.backup.verify_schedule}
Persistent=true
RandomizedDelaySec=1800

[Install]
WantedBy=timers.target
"""
    return {
        SERVICE_UNIT: service,
        TIMER_UNIT: timer,
        VERIFY_SERVICE_UNIT: verify_service,
        VERIFY_TIMER_UNIT: verify_timer,
    }


# --- Parsing --------------------------------------------------------------


def parse_queue_state(text: str) -> QueueState:
    """Read one sample of the queue from collected output.

    Sections are marked, so the output stays parseable without assuming an
    order or that every command exists.
    """
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("##"):
            current = line[2:].strip().lower()
            sections[current] = []
        elif current and line.strip():
            sections[current].append(line.strip())

    pending: int | None = None
    for line in sections.get("pending", []):
        if line.isdigit():
            pending = int(line)
            break

    active: int | None = None
    active_lines = sections.get("active", [])
    if any("- empty -" in line for line in active_lines):
        active = 0
    elif active_lines:
        # Celery prints one bullet per running task under each worker.
        counted = sum(1 for line in active_lines if line.startswith("*"))
        active = counted if counted else None

    return QueueState(pending=pending, active=active)


def parse_snapshots(json_text: str) -> list[Snapshot]:
    """`restic snapshots --json` into dataclasses, newest first."""
    if not json_text.strip():
        return []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise BackupError(f"Could not read the snapshot list from restic: {exc}") from exc
    if not isinstance(data, list):
        raise BackupError("Expected a list of snapshots from restic.")
    snapshots = [
        Snapshot(
            id=str(entry.get("id", "")),
            time=str(entry.get("time", "")),
            hostname=str(entry.get("hostname", "")),
            tags=list(entry.get("tags") or []),
            paths=list(entry.get("paths") or []),
        )
        for entry in data
        if isinstance(entry, dict)
    ]
    return sorted(snapshots, key=lambda s: s.time, reverse=True)


def latest_snapshot(snapshots: list[Snapshot], tag: str = SNAPSHOT_TAG) -> Snapshot | None:
    for snapshot in snapshots:
        if tag in snapshot.tags:
            return snapshot
    return None


# --- Talking to the target ------------------------------------------------


def _run(target: Host, command: str, input_text: str | None = None, timeout: int = 120):
    return sshexec.run(target.ssh_args, command, timeout=timeout, input_text=input_text)


def _run_ok(target: Host, command: str, input_text: str | None = None, timeout: int = 120) -> str:
    result = _run(target, command, input_text, timeout)
    if not result.ok:
        raise BackupError(
            f"Remote command failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _write_remote_file(target: Host, path: str, content: str, mode: str = "0644") -> None:
    """Write via stdin, so contents never reach argv or a temporary file."""
    quoted = shlex.quote(path)
    _run_ok(target, f"sudo tee {quoted} > /dev/null && sudo chmod {mode} {quoted}", content)
    _run_ok(target, f"sudo chown root:root {quoted}")


def install(cfg: config.Config, secrets: config.Secrets, target: Host) -> None:
    """Write the environment, the script and the units, then enable the timer."""
    if not storage.status(cfg, target).is_mounted:
        raise BackupError(
            f"{storage.MOUNTPOINT} is not mounted — run `pless unlock` first. "
            "The backup environment holds a secret and belongs on the encrypted volume."
        )

    environment = render_backup_env(cfg, secrets)  # raises before anything is written
    _run_ok(target, f"sudo mkdir -p {STAGING_DIR} {EXPORT_DIR} {SCRIPT_DIR}")
    _write_remote_file(target, ENV_FILE, environment, mode="0600")
    _write_remote_file(target, SCRIPT_PATH, render_script(cfg), mode="0700")
    _write_remote_file(target, VERIFY_SCRIPT_PATH, render_verify_script(cfg), mode="0700")
    for path, content in render_units(cfg).items():
        _write_remote_file(target, path, content)
    _run_ok(target, "sudo systemctl daemon-reload")
    _run_ok(target, "sudo systemctl enable --now pless-backup.timer")
    _run_ok(target, "sudo systemctl enable --now pless-backup-verify.timer")


def initialise_repository(target: Host) -> bool:
    """`restic init`, unless the repository already exists. True if created."""
    existing = _run(target, f"sudo sh -c '. {ENV_FILE} && restic cat config > /dev/null 2>&1'")
    if existing.ok:
        return False
    result = _run(target, f"sudo sh -c '. {ENV_FILE} && restic init'", timeout=300)
    if not result.ok:
        raise BackupError(
            "Could not initialise the restic repository: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return True


def run(target: Host, timeout: int = 21600) -> BackupRun:
    """Run the pipeline now, by invoking the same script the timer invokes.

    The default timeout is six hours: a first snapshot of several gigabytes
    over a domestic uplink is slow, and aborting it halfway is worse than
    waiting.
    """
    result = _run(target, f"sudo sh {SCRIPT_PATH}", timeout=timeout)
    if result.exit_code == EXIT_SKIPPED:
        return BackupRun(
            outcome=RunOutcome.SKIPPED_LOCKED,
            detail="The encrypted volume is not mounted. Run `pless unlock` and try again.",
        )
    record = read_run_record(target)
    if record is None:
        raise BackupError(
            "The backup script left no record behind. "
            f"Check `journalctl -u pless-backup.service` on the target. "
            f"{result.stderr.strip()}"
        )
    if not result.ok and record.outcome is not RunOutcome.FAILED:
        # Trust the exit code over a record that disagrees with it.
        record.outcome = RunOutcome.FAILED
    return record


def export_only(cfg: config.Config, target: Host) -> str:
    """Run the document exporter alone, without a snapshot."""
    delete_flag = " --delete" if cfg.backup.exporter_delete else ""
    return _run_ok(
        target,
        f"cd {composegen.INSTALL_DIR} && sudo docker compose exec -T webserver "
        f"document_exporter /usr/src/paperless/export --no-progress-bar "
        f"--split-manifest{delete_flag}",
        timeout=7200,
    )


def verify(target: Host, timeout: int = 3600) -> VerificationRecord:
    """Verify a restore now, by invoking the script the timer invokes.

    A failing verification is a *result*, not an exception: the record it wrote
    is the answer, and `preflight` has to be able to read it either way.
    """
    result = _run(target, f"sudo sh {VERIFY_SCRIPT_PATH}", timeout=timeout)
    if result.exit_code == EXIT_SKIPPED:
        raise BackupError(
            "The encrypted volume is not mounted, so there is nothing to verify. "
            "Run `pless unlock` and try again."
        )
    record = read_verification_record(target)
    if record is None:
        raise BackupError(
            "Verification left no record behind. Check "
            "`journalctl -u pless-backup-verify.service` on the target. "
            f"{result.stderr.strip()}"
        )
    return record


def read_verification_record(target: Host) -> VerificationRecord | None:
    result = _run(target, f"sudo cat {VERIFY_RECORD} 2>/dev/null")
    if not result.ok or not result.stdout.strip():
        return None
    return VerificationRecord.from_json(result.stdout)


def is_verified(cfg: config.Config, target: Host, now: datetime | None = None) -> bool:
    """Has a restore succeeded recently enough to still mean something?

    Silence reads as "no". A missing record, an unreadable one, a failed one and
    a stale one are all the same answer to `preflight`, which is the only honest
    default when the question is whether the documents can be got back.
    """
    try:
        record = read_verification_record(target)
    except BackupError:
        return False
    if record is None or not record.passed:
        return False
    return not record.is_stale(cfg.backup.verify_max_age_days, now or datetime.now(UTC))


def read_run_record(target: Host) -> BackupRun | None:
    result = _run(target, f"sudo cat {RUN_RECORD} 2>/dev/null")
    if not result.ok or not result.stdout.strip():
        return None
    return BackupRun.from_json(result.stdout)


def queue_state(target: Host) -> QueueState:
    """One sample of the task queue, for status output."""
    script = f"""\
cd {composegen.INSTALL_DIR}
echo "##PENDING"
sudo docker compose exec -T broker redis-cli -n 0 LLEN celery 2>/dev/null || true
echo "##ACTIVE"
sudo docker compose exec -T webserver celery -A paperless inspect active 2>/dev/null || true
"""
    result = _run(target, "sh -s", input_text=script)
    return parse_queue_state(result.stdout)


def snapshots(target: Host) -> list[Snapshot]:
    result = _run(target, f"sudo sh -c '. {ENV_FILE} && restic snapshots --json'", timeout=300)
    if not result.ok:
        raise BackupError(
            "Could not list snapshots: " + (result.stderr.strip() or result.stdout.strip())
        )
    return parse_snapshots(result.stdout)


def timer_state(target: Host) -> str:
    """'active', 'inactive', or 'not installed'."""
    result = _run(target, "systemctl is-active pless-backup.timer 2>/dev/null")
    state = result.stdout.strip()
    if not state:
        return "not installed"
    return state
