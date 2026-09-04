"""The full restore rehearsal, as wiring.

Nothing here talks to a hypervisor: what these tests hold is the order of the
steps, where the record ends up, and — most of all — the refusals. A rehearsal
has two ways to be worse than useless. It can overwrite a good verification
record because the *operator's laptop* was missing a tool, which would make
`pless preflight` report a problem the backup does not have. And it can repoint
`[host]` at a machine that is about to be destroyed. Both have tests.

The fakes carry the signatures of what they stand in for, and `TestTheFakes`
holds them to it.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pless import backup, bootstrap, config, deploy, drill, hostfile, storage, vm
from pless.targets import Host

A_HOST = Host(label="arkiv-01", ssh_args=["-p", "22", "deploy@nowhere.invalid"])

A_SNAPSHOT = backup.Snapshot(id="42e784456fbc1a2d", time="2026-09-03T02:00:00Z", tags=["pless"])


def a_config(repository: str = "b2:archive-bucket:paperless") -> config.Config:
    cfg = config.Config()
    cfg.backup = config.BackupConfig(restic_repository=repository)
    return cfg


def some_secrets() -> config.Secrets:
    return config.Secrets(
        restic_password="9K2M4-XR7TQ-B8HNV-5WGDC-3PFJZ",
        b2_key_id="id-0001",
        b2_application_key="key-0001",
    )


@dataclass
class World:
    """What the rehearsal did to the world outside it."""

    launched: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    bootstrapped: list[str] = field(default_factory=list)
    records: list[tuple[str, backup.VerificationRecord]] = field(default_factory=list)
    restored_snapshots: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    documents_after_restore: int = 31
    run_record_documents: int = 31


def a_working_world(monkeypatch: pytest.MonkeyPatch, world: World | None = None) -> World:
    """Every step succeeds. Individual tests break exactly one of them."""
    world = world or World()

    monkeypatch.setattr(vm, "require_backend", lambda backend: None)
    monkeypatch.setattr(vm, "exists", lambda cfg: False)
    monkeypatch.setattr(
        vm, "launch", lambda cfg, user_data_path=None: world.launched.append(cfg.name)
    )
    monkeypatch.setattr(vm, "delete", lambda cfg: world.deleted.append(cfg.name))
    monkeypatch.setattr(
        vm,
        "ssh_config_for",
        lambda cfg, ssh_key: (Path("/tmp/ssh_config"), cfg.name),
    )
    monkeypatch.setattr(drill, "_wait_for_ssh", lambda target, timeout_seconds=300: None)
    monkeypatch.setattr(
        bootstrap, "apply", lambda cfg, target, facts=None: world.bootstrapped.append(target.label)
    )
    monkeypatch.setattr(
        storage, "init", lambda cfg, target, passphrase: world.steps.append("storage") or "/dev/sdb"
    )
    monkeypatch.setattr(
        deploy, "install", lambda cfg, secrets, target: world.steps.append("deploy")
    )
    monkeypatch.setattr(deploy, "wait_healthy", lambda target, timeout_seconds=300: True)
    monkeypatch.setattr(backup, "snapshots", lambda target: [A_SNAPSHOT])
    monkeypatch.setattr(
        backup,
        "read_run_record",
        lambda target: backup.BackupRun(
            outcome=backup.RunOutcome.SUCCEEDED,
            snapshot_id=A_SNAPSHOT.id,
            documents_exported=world.run_record_documents,
        ),
    )
    monkeypatch.setattr(
        backup, "install_environment", lambda cfg, secrets, target: world.steps.append("env")
    )

    def restore(cfg, target, snapshot_id="latest"):
        world.restored_snapshots.append(snapshot_id)
        world.steps.append("restore")
        return world.documents_after_restore

    monkeypatch.setattr(backup, "restore", restore)
    monkeypatch.setattr(
        backup,
        "write_verification_record",
        lambda target, record: world.records.append((target.label, record)),
    )
    return world


class TestTheDrillVm:
    def test_is_the_operators_own_settings_under_another_name(self) -> None:
        cfg = a_config()
        cfg.vm = config.VmConfig(backend="multipass", name="arkiv", cpus=4, memory="8G")
        drill_vm = drill.drill_vm_config(cfg)

        assert drill_vm.name == "arkiv-drill"
        assert (drill_vm.backend, drill_vm.cpus, drill_vm.memory) == ("multipass", 4, "8G")
        assert cfg.vm.name == "arkiv", "it mutated the operator's own VM configuration"


class TestRefusalsThatWriteNoRecord:
    """ "The drill could not be run" is not "the restore failed"."""

    def test_a_local_repository_cannot_be_reached_by_a_second_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        with pytest.raises(drill.DrillError, match="local path"):
            drill.verify_full(a_config("/mnt/backup/restic"), some_secrets(), A_HOST)
        assert world.launched == []
        assert world.records == [], "it clobbered a verification record over a local repository"

    def test_an_unconfigured_repository_stops_before_anything_is_created(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        with pytest.raises(drill.DrillError, match="nothing to restore from"):
            drill.verify_full(a_config(""), some_secrets(), A_HOST)
        assert world.launched == []
        assert world.records == []

    def test_an_empty_repository_is_named_as_the_dangerous_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        monkeypatch.setattr(backup, "snapshots", lambda target: [])
        with pytest.raises(drill.DrillError, match="recreated"):
            drill.verify_full(a_config(), some_secrets(), A_HOST)
        assert world.launched == []
        assert world.records == []

    def test_an_unknown_snapshot_is_refused_before_a_vm_is_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        with pytest.raises(drill.DrillError, match="No snapshot in the repository"):
            drill.verify_full(a_config(), some_secrets(), A_HOST, "deadbeef")
        assert world.launched == []

    def test_a_leftover_drill_vm_is_not_silently_reused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        monkeypatch.setattr(vm, "exists", lambda vm_cfg: True)
        with pytest.raises(drill.DrillError, match="already exists"):
            drill.verify_full(a_config(), some_secrets(), A_HOST)
        assert world.launched == []
        assert world.deleted == [], "it destroyed a machine an earlier failure left for inspection"


class TestAPassingRehearsal:
    def test_builds_restores_records_and_destroys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = a_working_world(monkeypatch)
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert result.record.passed
        assert result.record.level is backup.VerificationLevel.FULL
        assert world.steps == ["storage", "deploy", "env", "restore"]
        assert result.vm_destroyed and world.deleted == ["pless-dev-drill"]

    def test_the_record_lands_on_the_target_that_owns_the_archive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR 0019: not on the machine that was destroyed proving it."""
        world = a_working_world(monkeypatch)
        drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert [label for label, _ in world.records] == [A_HOST.label]

    def test_the_record_names_the_snapshot_rather_than_latest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record saying "latest" could not be checked against the repository."""
        world = a_working_world(monkeypatch)
        result = drill.verify_full(a_config(), some_secrets(), A_HOST, "latest")

        assert result.record.snapshot_id == A_SNAPSHOT.id
        assert world.restored_snapshots == [A_SNAPSHOT.id]

    def test_it_never_repoints_the_host_in_pless_toml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`vm create` rewrites [host]; a machine that lives 20 minutes must not."""

        def explode(*args, **kwargs):
            raise AssertionError("the rehearsal rewrote [host] to point at a throwaway VM")

        monkeypatch.setattr(hostfile, "apply", explode)
        a_working_world(monkeypatch)
        drill.verify_full(a_config(), some_secrets(), A_HOST)


class TestAFailingRehearsal:
    def test_a_wrong_count_fails_and_names_both_numbers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        world.documents_after_restore = 29
        world.run_record_documents = 31
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert not result.record.passed
        assert "29" in result.record.detail and "31" in result.record.detail
        assert result.record.documents_found == 29
        assert result.record.documents_expected == 31

    def test_restoring_nothing_is_a_failure_not_a_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        world.documents_after_restore = 0
        world.run_record_documents = 0
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert not result.record.passed
        assert "no documents" in result.record.detail

    def test_a_break_after_the_vm_exists_is_recorded_as_a_failed_restore(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        monkeypatch.setattr(deploy, "wait_healthy", lambda target, timeout_seconds=300: False)
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert not result.record.passed
        assert world.records and not world.records[0][1].passed
        assert "did not complete" in result.record.detail

    def test_a_failed_rehearsal_leaves_its_machine_for_inspection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)
        monkeypatch.setattr(deploy, "wait_healthy", lambda target, timeout_seconds=300: False)
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert not result.vm_destroyed
        assert world.deleted == [], "it destroyed the evidence"
        assert result.vm_name == "pless-dev-drill"

    def test_a_restore_that_raises_still_leaves_a_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = a_working_world(monkeypatch)

        def refuse(cfg, target, snapshot_id="latest"):
            raise backup.BackupError("already holds 43 documents")

        monkeypatch.setattr(backup, "restore", refuse)
        result = drill.verify_full(a_config(), some_secrets(), A_HOST)

        assert not result.record.passed
        assert "43 documents" in result.record.detail
        assert len(world.records) == 1


class TestTheFakes:
    """A fake with the wrong signature is a test that passes while the code breaks."""

    @pytest.mark.parametrize(
        ("real", "fake"),
        [
            (vm.launch, lambda cfg, user_data_path=None: None),
            (vm.delete, lambda cfg: None),
            (vm.exists, lambda cfg: False),
            (vm.ssh_config_for, lambda cfg, ssh_key: None),
            (bootstrap.apply, lambda cfg, target, facts=None: None),
            (storage.init, lambda cfg, target, passphrase: None),
            (deploy.install, lambda cfg, secrets, target: None),
            (deploy.wait_healthy, lambda target, timeout_seconds=300: True),
            (backup.snapshots, lambda target: []),
            (backup.read_run_record, lambda target: None),
            (backup.install_environment, lambda cfg, secrets, target: None),
            (backup.restore, lambda cfg, target, snapshot_id="latest": 0),
            (backup.write_verification_record, lambda target, record: None),
        ],
    )
    def test_the_fake_takes_what_the_real_function_takes(self, real, fake) -> None:
        real_parameters = list(inspect.signature(real).parameters)
        fake_parameters = list(inspect.signature(fake).parameters)
        assert real_parameters == fake_parameters, (
            f"{real.__name__} takes {real_parameters}, the fake takes {fake_parameters}"
        )
