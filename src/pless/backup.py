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
from enum import StrEnum

from pless import composegen, config, sshexec, storage
from pless.targets import Host

STAGING_DIR = f"{composegen.INSTALL_DIR}/backups"
EXPORT_DIR = f"{composegen.INSTALL_DIR}/export"
ENV_FILE = f"{composegen.INSTALL_DIR}/backup.env"
SCRIPT_PATH = f"{composegen.INSTALL_DIR}/pless-backup.sh"
RUN_RECORD = f"{STAGING_DIR}/last-run.json"

SERVICE_UNIT = "/etc/systemd/system/pless-backup.service"
TIMER_UNIT = "/etc/systemd/system/pless-backup.timer"

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
    if cfg.backup.repository_kind == "b2":
        if not secrets.b2_account_id:
            missing.append("B2_ACCOUNT_ID in .env")
        if not secrets.b2_account_key:
            missing.append("B2_ACCOUNT_KEY in .env")
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
    if cfg.backup.repository_kind == "b2":
        lines.append(f"B2_ACCOUNT_ID={_shell_quote(secrets.b2_account_id)}")
        lines.append(f"B2_ACCOUNT_KEY={_shell_quote(secrets.b2_account_key)}")
    lines.append("export RESTIC_REPOSITORY RESTIC_PASSWORD")
    if cfg.backup.repository_kind == "b2":
        lines.append("export B2_ACCOUNT_ID B2_ACCOUNT_KEY")
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


def render_units(cfg: config.Config) -> dict[str, str]:
    """Unit and timer, keyed by their absolute path on the target."""
    service = f"""\
[Unit]
Description=pless backup (export, database dump, restic snapshot)
After=paperless.service
# Refuses to run unless the encrypted volume is mounted.
RequiresMountsFor={composegen.INSTALL_DIR}

[Service]
Type=oneshot
WorkingDirectory={composegen.INSTALL_DIR}
ExecStart=/bin/sh {SCRIPT_PATH}
# A locked volume is a skip, not a failure.
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
    return {SERVICE_UNIT: service, TIMER_UNIT: timer}


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
    _run_ok(target, f"sudo mkdir -p {STAGING_DIR} {EXPORT_DIR}")
    _write_remote_file(target, ENV_FILE, environment, mode="0600")
    _write_remote_file(target, SCRIPT_PATH, render_script(cfg), mode="0700")
    for path, content in render_units(cfg).items():
        _write_remote_file(target, path, content)
    _run_ok(target, "sudo systemctl daemon-reload")
    _run_ok(target, "sudo systemctl enable --now pless-backup.timer")


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
