"""Restore and the rehearsal, driven through the CLI.

The wiring in `cli.py` is where drift hides: every module below it is covered
by tests over pure functions, and the bug that made `pless preflight` die
before checking anything lived here rather than in `preflight.py`. So these
tests exercise the real command objects, and fake only the layer that would
touch a machine.

What they mostly hold is that the destructive command asks first — and that
saying nothing, or the wrong thing, does not count as saying yes.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from pless import backup, cli, config, drill

runner = CliRunner()


def _configured(path=None) -> config.Config:
    cfg = config.Config(host=config.HostConfig(address="nowhere.invalid", user="deploy"))
    cfg.backup = config.BackupConfig(restic_repository="b2:archive-bucket:paperless")
    return cfg


def _secrets() -> config.Secrets:
    return config.Secrets(
        restic_password="9K2M4-XR7TQ-B8HNV-5WGDC-3PFJZ",
        b2_key_id="id-0001",
        b2_application_key="key-0001",
    )


@pytest.fixture(autouse=True)
def _local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_config", _configured)
    monkeypatch.setattr(config, "load_secrets", _secrets)


def invoke(args: list[str], user_input: str | None = None) -> Result:
    return runner.invoke(cli.app, args, input=user_input)


class TestRestoreAsksFirst:
    def test_without_confirm_it_does_not_touch_the_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            backup, "restore", lambda cfg, target, snapshot_id="latest": calls.append(snapshot_id)
        )
        result = invoke(["backup", "restore"])

        assert result.exit_code != 0
        assert "--confirm" in result.output
        assert calls == []

    def test_the_wrong_label_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            backup, "restore", lambda cfg, target, snapshot_id="latest": calls.append(snapshot_id)
        )
        result = invoke(["backup", "restore", "--confirm"], user_input="some-other-host\n")

        assert result.exit_code != 0
        assert "did not match" in result.output
        assert calls == [], "it restored after the operator typed the wrong name"

    def test_the_right_label_restores_the_requested_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def restore(cfg, target, snapshot_id="latest"):
            calls.append(snapshot_id)
            return 31

        monkeypatch.setattr(backup, "restore", restore)
        result = invoke(
            ["backup", "restore", "--snapshot", "42e78445", "--confirm"],
            user_input=f"{_configured().host.label}\n",
        )

        assert result.exit_code == 0, result.output
        assert calls == ["42e78445"]
        assert "31 documents restored" in result.output

    def test_a_refusal_from_the_core_reaches_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(cfg, target, snapshot_id="latest"):
            raise backup.BackupError("arkiv-01 already holds 43 documents")

        monkeypatch.setattr(backup, "restore", refuse)
        result = invoke(
            ["backup", "restore", "--confirm"], user_input=f"{_configured().host.label}\n"
        )

        assert result.exit_code != 0
        assert "already holds 43 documents" in result.output


class TestVerifyLevels:
    def test_an_unknown_level_is_refused(self) -> None:
        result = invoke(["backup", "verify", "--level", "thorough"])
        assert result.exit_code != 0
        assert "'content' or 'full'" in result.output

    def test_full_runs_the_rehearsal_and_reports_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = backup.VerificationRecord(
            level=backup.VerificationLevel.FULL,
            passed=True,
            snapshot_id="42e784456f",
            detail="A new machine produced 31 documents from snapshot 42e78445.",
        )
        monkeypatch.setattr(
            drill,
            "verify_full",
            lambda cfg, secrets, target, snapshot_id="latest", progress=None: drill.DrillResult(
                record=record, vm_name="pless-dev-drill", vm_destroyed=True
            ),
        )
        result = invoke(["backup", "verify", "--level", "full"])

        assert result.exit_code == 0, result.output
        assert "31 documents" in result.output

    def test_a_failed_rehearsal_says_the_machine_is_still_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = backup.VerificationRecord(
            level=backup.VerificationLevel.FULL, passed=False, detail="The rehearsal broke."
        )
        monkeypatch.setattr(
            drill,
            "verify_full",
            lambda cfg, secrets, target, snapshot_id="latest", progress=None: drill.DrillResult(
                record=record, vm_name="pless-dev-drill", vm_destroyed=False
            ),
        )
        result = invoke(["backup", "verify", "--level", "full"])

        assert result.exit_code == 1
        assert "pless-dev-drill was left running" in result.output

    def test_a_refusal_to_rehearse_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(cfg, secrets, target, snapshot_id="latest", progress=None):
            raise drill.DrillError("The repository is a local path on the target")

        monkeypatch.setattr(drill, "verify_full", refuse)
        result = invoke(["backup", "verify", "--level", "full"])

        assert result.exit_code != 0
        assert "local path" in result.output
        assert "Traceback" not in result.output


class TestExtractAsksFirst:
    def test_without_confirm_it_names_the_destination_and_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Path] = []
        monkeypatch.setattr(
            backup,
            "extract",
            lambda cfg, target, destination, snapshot_id="latest": calls.append(destination),
        )
        result = invoke(["backup", "extract"])

        assert result.exit_code != 0
        assert "unencrypted disk" in result.output
        assert "ADR 0018" in result.output
        assert calls == []

    def test_with_confirm_it_extracts_where_told(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[Path, str]] = []

        def extract(cfg, target, destination, snapshot_id="latest"):
            calls.append((destination, snapshot_id))
            return destination

        monkeypatch.setattr(backup, "extract", extract)
        result = invoke(
            [
                "backup",
                "extract",
                "--to",
                "/tmp/pless-extract-test",
                "--snapshot",
                "42e78445",
                "--confirm",
            ]
        )

        assert result.exit_code == 0, result.output
        # Resolved, not as typed: the message before --confirm has to name where
        # plaintext actually lands, and on macOS /tmp is a symlink.
        assert calls == [(Path("/tmp/pless-extract-test").resolve(), "42e78445")]

    def test_it_says_the_result_is_not_a_re_importable_export(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            backup, "extract", lambda cfg, target, destination, snapshot_id="latest": destination
        )
        result = invoke(["backup", "extract", "--confirm"])
        assert "not a re-importable export" in result.output


class TestForgetIsADryRunUntilItIsNot:
    def _fake_forget(self, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
        calls: list[bool] = []

        def forget(cfg, target, dry_run=True):
            calls.append(dry_run)
            return "keep 7 snapshots"

        monkeypatch.setattr(backup, "forget", forget)
        return calls

    def test_the_default_removes_nothing_and_needs_no_confirmation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._fake_forget(monkeypatch)
        result = invoke(["backup", "forget"])

        assert result.exit_code == 0, result.output
        assert calls == [True]
        assert "Dry run" in result.output
        assert "--prune --confirm" in result.output

    def test_prune_without_confirm_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_forget(monkeypatch)
        result = invoke(["backup", "forget", "--prune"])

        assert result.exit_code != 0
        assert "--confirm" in result.output
        assert calls == []

    def test_the_wrong_repository_name_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_forget(monkeypatch)
        result = invoke(
            ["backup", "forget", "--prune", "--confirm"], user_input="some-other-bucket\n"
        )

        assert result.exit_code != 0
        assert "did not match" in result.output
        assert calls == [], "it pruned after the operator typed the wrong name"

    def test_the_right_repository_name_prunes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_forget(monkeypatch)
        label = backup.repository_label(_configured().backup.restic_repository)
        result = invoke(["backup", "forget", "--prune", "--confirm"], user_input=f"{label}\n")

        assert result.exit_code == 0, result.output
        assert calls == [False]

    def test_it_explains_why_pruning_may_reclaim_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under Object Lock that is the price of immutability, not a failure."""
        self._fake_forget(monkeypatch)
        label = backup.repository_label(_configured().backup.restic_repository)
        result = invoke(["backup", "forget", "--prune", "--confirm"], user_input=f"{label}\n")
        assert "reclaims nothing" in result.output


class TestTheFakes:
    @pytest.mark.parametrize(
        ("real", "fake"),
        [
            (backup.restore, lambda cfg, target, snapshot_id="latest": 0),
            (
                backup.extract,
                lambda cfg, target, destination, snapshot_id="latest": destination,
            ),
            (backup.forget, lambda cfg, target, dry_run=True: ""),
            (
                drill.verify_full,
                lambda cfg, secrets, target, snapshot_id="latest", progress=None: None,
            ),
            (config.load_config, _configured),
            (config.load_secrets, _secrets),
        ],
    )
    def test_the_fake_takes_what_the_real_function_takes(self, real, fake) -> None:
        assert list(inspect.signature(real).parameters) == list(inspect.signature(fake).parameters)
