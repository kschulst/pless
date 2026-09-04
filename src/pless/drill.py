"""The full restore rehearsal: a throwaway machine, restored from the repository.

Content verification proves the *data* comes back. This proves the *procedure*
does: a machine that never held the archive is created, bootstrapped, given an
encrypted volume and a Paperless stack, and then made to produce the documents
from nothing but the repository and the secrets. It is the only check that
would notice a step missing from the restore path itself, because every other
check starts from a machine that is already set up.

It lives here rather than in `backup` on purpose. `backup` is deliberately a
low-level module — `config`, `sshexec`, `composegen`, `storage` — and a
rehearsal needs `vm`, `bootstrap` and `deploy` as well. Putting it there would
have inverted the layering and made `backup` depend on nearly everything;
putting it above lets both stay honest. Like `backup`, this module imports
neither typer nor rich: progress reaches the operator through a callback.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pless import (
    backup,
    bootstrap,
    config,
    deploy,
    hostspec,
    secretgen,
    sshexec,
    storage,
    vm,
)
from pless.targets import Host

# The rehearsal machine is named after the one it rehearses for, so an
# interrupted drill leaves something obviously disposable behind rather than
# something the operator has to identify.
DRILL_SUFFIX = "-drill"

SSH_WAIT_SECONDS = 300
HEALTH_WAIT_SECONDS = 900


class DrillError(RuntimeError):
    pass


@dataclass
class DrillResult:
    """What the rehearsal proved, and what it left behind."""

    record: backup.VerificationRecord
    vm_name: str
    vm_destroyed: bool


def drill_vm_config(cfg: config.Config) -> config.VmConfig:
    """The throwaway machine: the operator's own VM settings, under another name."""
    return cfg.vm.model_copy(update={"name": f"{cfg.vm.name}{DRILL_SUFFIX}"})


def _wait_for_ssh(target: Host, timeout_seconds: int = SSH_WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if sshexec.run(target.ssh_args, "true", timeout=15).ok:
            return
        time.sleep(5)
    raise DrillError(
        f"{target.label} did not accept an SSH connection within {timeout_seconds}s. "
        "The rehearsal machine was created but cannot be reached."
    )


def _provision(cfg: config.Config, vm_cfg: config.VmConfig) -> Host:
    """Create the rehearsal machine and return a way to reach it.

    Deliberately does not touch `pless.toml`. `pless vm create` repoints
    `[host]` at what it made, which is right for a machine you are about to
    use and catastrophic for one that exists for twenty minutes: a drill that
    rewrote `[host]` would leave every later command pointing at a VM that no
    longer exists.
    """
    vm.require_backend(vm_cfg.backend)
    if vm.exists(vm_cfg):
        raise DrillError(
            f"A VM named {vm_cfg.name!r} already exists — an earlier rehearsal probably "
            f"failed and left it. Inspect it, then remove it with your VM tool before "
            "running another drill."
        )

    user_data_path: Path | None = None
    if vm_cfg.backend == "multipass":
        pubkey = hostspec.read_pubkey(cfg.host.key)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(hostspec.render_user_data(pubkey, cfg.paperless.timezone))
            user_data_path = Path(handle.name)
    try:
        vm.launch(vm_cfg, user_data_path)
    finally:
        if user_data_path:
            user_data_path.unlink(missing_ok=True)

    ssh_config, alias = vm.ssh_config_for(vm_cfg, cfg.host.key)
    target = Host(label=alias, ssh_args=["-F", str(ssh_config), alias])
    _wait_for_ssh(target)

    if vm_cfg.backend == "multipass":
        # cloud-init is the provisioner here, and it holds the apt lock until it
        # is done. Anything installing packages before that fails for reasons
        # that look nothing like the real cause.
        sshexec.run(
            target.ssh_args, "cloud-init status --wait > /dev/null 2>&1 || true", timeout=900
        )
    else:
        bootstrap.apply(cfg, target)
    return target


def _resolve_snapshot(target: Host, snapshot_id: str) -> backup.Snapshot:
    """Turn 'latest' into a concrete id, so the record can name what it restored."""
    snaps = backup.snapshots(target)
    if snapshot_id == "latest":
        latest = backup.latest_snapshot(snaps)
        if latest is None:
            raise DrillError(
                f"No snapshots tagged {backup.SNAPSHOT_TAG} in the repository. Either "
                "nothing has been backed up yet, or the repository was recreated — the "
                "second looks like success and holds nothing. Run `pless backup run` first."
            )
        return latest
    for snap in snaps:
        if snap.id.startswith(snapshot_id):
            return snap
    raise DrillError(
        f"No snapshot in the repository starts with {snapshot_id!r}. "
        "`pless backup status` lists what is there."
    )


def verify_full(
    cfg: config.Config,
    secrets: config.Secrets,
    target: Host,
    snapshot_id: str = "latest",
    progress: Callable[[str], None] | None = None,
) -> DrillResult:
    """Restore into a throwaway VM, and record the result on the original target.

    The record is written where `preflight` reads it (ADR 0019) — on the target
    that owns the archive, not on the machine that was destroyed proving it.
    """
    say = progress or (lambda _message: None)

    # These refusals come before anything is created, and deliberately do not
    # write a record. "The drill could not be run" is not "the restore failed",
    # and letting a missing VM tool overwrite a good content verification would
    # make `preflight` report a problem the backup does not have.
    if not cfg.backup.is_configured:
        raise DrillError(
            "[backup] restic_repository is empty, so there is nothing to restore from."
        )
    if cfg.backup.is_local_repository:
        raise DrillError(
            "The repository is a local path on the target, which a separate machine "
            "cannot reach — so a full rehearsal would prove nothing about it. Use "
            "`pless backup verify --level content`, which restores on the target itself."
        )

    say("Resolving the snapshot to restore…")
    snapshot = _resolve_snapshot(target, snapshot_id)
    run_record = backup.read_run_record(target)
    expected = 0
    expectation_note = ""
    if run_record and run_record.snapshot_id == snapshot.id:
        expected = run_record.documents_exported
    else:
        expectation_note = (
            " No run record names this snapshot, so the document count could not be "
            "compared against what produced it."
        )

    vm_cfg = drill_vm_config(cfg)
    performed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def record_for(passed: bool, found: int, detail: str) -> backup.VerificationRecord:
        return backup.VerificationRecord(
            level=backup.VerificationLevel.FULL,
            performed_at=performed_at,
            snapshot_id=snapshot.id,
            repository_kind=cfg.backup.repository_kind,
            passed=passed,
            documents_expected=expected,
            documents_found=found,
            # The whole archive was restored, so the sample is everything.
            sample_size=found,
            detail=detail,
        )

    say(f"Creating {vm_cfg.name} via {vm_cfg.backend}. This takes a while…")
    drill_target = _provision(cfg, vm_cfg)

    # From here the rehearsal machine exists, so every exit writes a record: a
    # failure past this line is something we learned about the restore path.
    destroyed = False
    try:
        say("Creating an encrypted volume…")
        # Thrown away with the machine — it protects data that exists for
        # twenty minutes and is never written down anywhere.
        storage.init(cfg, drill_target, secretgen.human_passphrase())

        say("Deploying Paperless…")
        deploy.install(cfg, secrets, drill_target)
        if not deploy.wait_healthy(drill_target, timeout_seconds=HEALTH_WAIT_SECONDS):
            raise DrillError(
                "Paperless did not answer on the rehearsal machine within "
                f"{HEALTH_WAIT_SECONDS}s, so the restore never started."
            )

        say(f"Restoring snapshot {snapshot.short_id} and importing it…")
        backup.install_environment(cfg, secrets, drill_target)
        found = backup.restore(cfg, drill_target, snapshot.id)

        if found == 0:
            passed, detail = False, "The restore imported no documents at all."
        elif expected and found != expected:
            passed, detail = (
                False,
                f"Restored {found} documents; the run that produced snapshot "
                f"{snapshot.short_id} recorded {expected}.",
            )
        else:
            passed, detail = (
                True,
                f"A new machine was built from nothing and produced {found} documents "
                f"from snapshot {snapshot.short_id}.{expectation_note}",
            )
        record = record_for(passed, found, detail)
    except (
        DrillError,
        backup.BackupError,
        bootstrap.BootstrapError,
        deploy.DeployError,
        storage.StorageError,
        vm.VmError,
    ) as exc:
        record = record_for(False, 0, f"The rehearsal did not complete: {exc}")
        backup.write_verification_record(target, record)
        # Left standing on purpose: a failed rehearsal is the one case where the
        # machine is worth looking at, and destroying it destroys the evidence.
        return DrillResult(record=record, vm_name=vm_cfg.name, vm_destroyed=False)

    backup.write_verification_record(target, record)
    say(f"Destroying {vm_cfg.name}…")
    vm.delete(vm_cfg)
    destroyed = True
    return DrillResult(record=record, vm_name=vm_cfg.name, vm_destroyed=destroyed)
