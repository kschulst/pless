"""Preflight, driven through the CLI.

`preflight` is the command that decides whether an installation can be trusted
with documents, and it was reaching for `target.user` on a `Host` that has had
no such attribute since the host became a label and a list of SSH arguments —
so it died with `AttributeError` before checking anything. Every other module is
covered by tests over pure functions, which is exactly why the wiring in
`cli.py` was where the drift hid.

Wiring is what these tests exercise, so they fake as little as possible: the
real `targets`, `storage`, `deploy` and `audit` code runs, and only the SSH
round trip and the config file are replaced. The fakes carry the same signatures
as the functions they stand in for, and `TestTheFakes` holds them to it —
otherwise the day `sshexec.run` changes again is the day these tests keep
passing while the command breaks.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from pless import cli, config, sshexec, storage

runner = CliRunner()


def _load_config(path: Path | None = None) -> config.Config:
    """A configured host, so `_host` resolves through the real `targets` code."""
    return config.Config(host=config.HostConfig(address="nowhere.invalid", user="deploy"))


def _no_answer(
    destination: list[str],
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
) -> sshexec.SshResult:
    """A host that resolves but does not answer."""
    return sshexec.SshResult(exit_code=255, stdout="", stderr="ssh: connect timed out")


# What a healthy machine says when the audit collector runs: nothing listening
# off loopback, UFW closed, no container publishing to the LAN, keys only, and
# the data directory on the LUKS device.
CLEAN_AUDIT = f"""\
##LISTEN
LISTEN 0 128 127.0.0.1:22 0.0.0.0:*
##UFW
Status: active
Default: deny (incoming), allow (outgoing), disabled (routed)
##DOCKER
paperless-web 127.0.0.1:8000->8000/tcp
##SSHD
passwordauthentication no
permitrootlogin no
##MOUNT
/dev/mapper/{storage.MAPPER_NAME}
"""


def _answers(
    destination: list[str],
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
) -> sshexec.SshResult:
    """A host where everything works, answering each command as the real one would."""
    if input_text is not None:  # the audit collector, sent on stdin
        stdout = CLEAN_AUDIT
    elif "losetup" in remote_command:  # resolving the loop-backed data volume
        stdout = "/dev/loop0"
    elif "curl" in remote_command:  # Paperless on the target's localhost
        stdout = "200"
    else:  # `true`, cryptsetup isLuks/status, mountpoint -q
        stdout = ""
    return sshexec.SshResult(exit_code=0, stdout=stdout, stderr="")


@pytest.fixture
def unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_config", _load_config)
    monkeypatch.setattr(sshexec, "run", _no_answer)


@pytest.fixture
def healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_config", _load_config)
    monkeypatch.setattr(sshexec, "run", _answers)


def _preflight(*args: str) -> Result:
    """Invoke the command and insist it ended by decision, not by traceback.

    `CliRunner` reports exit code 1 both for `typer.Exit(1)` and for an
    unhandled exception, and that ambiguity is what let the bug live: a command
    that crashes and a command that says NOT READY look alike from the outside.
    """
    result = runner.invoke(cli.app, ["preflight", *args])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result


class TestATargetThatHangs:
    """#13 — the clearest case for it: a machine that accepts the connection
    and then stops answering made `preflight` exit with a traceback instead of
    reporting NOT READY, at the exact moment someone was about to import
    documents."""

    @pytest.fixture
    def hanging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "load_config", _load_config)

        def hang(argv, capture_output=False, text=False, timeout=None, input=None):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout or 0)

        monkeypatch.setattr(subprocess, "run", hang)

    def test_it_decides_rather_than_crashing(self, hanging: None) -> None:
        result = _preflight()
        assert result.exit_code != 0
        assert "NOT READY" in result.output

    def test_it_says_the_target_did_not_answer(self, hanging: None) -> None:
        result = _preflight()
        assert "target reachable" in result.output


class TestWarningsDoNotWithholdAGreenLight:
    """A warning is worth knowing and does not disqualify. The one that used to
    block was "the volume is not mounted" — the normal state after a reboot, and
    something `preflight` checks for itself anyway."""

    def test_a_critical_audit_finding_still_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relaxing the gate must not relax it for the findings that matter."""
        exposed = CLEAN_AUDIT.replace("127.0.0.1:22", "0.0.0.0:8000")

        def answers(destination, remote_command, timeout=60, input_text=None):
            if input_text is not None:
                return sshexec.SshResult(exit_code=0, stdout=exposed, stderr="")
            return _answers(destination, remote_command, timeout, input_text)

        monkeypatch.setattr(config, "load_config", _load_config)
        monkeypatch.setattr(sshexec, "run", answers)
        result = _preflight()

        assert result.exit_code != 0
        assert "NOT READY" in result.output


class TestUnreachableTarget:
    def test_reports_the_target_rather_than_crashing(self, unreachable: None) -> None:
        result = _preflight()
        assert result.exit_code == 1  # blockers present, which is the point
        assert "target reachable" in result.output

    def test_says_what_is_wrong_rather_than_only_that_something_is(self, unreachable: None) -> None:
        result = _preflight()
        assert "No response over SSH" in result.output
        assert "NOT READY" in result.output

    def test_still_reports_the_missing_backup_capabilities(self, unreachable: None) -> None:
        # Silence here would read as approval, so the absent capabilities are
        # listed explicitly even when the target cannot be reached at all.
        result = _preflight()
        assert "restore verified" in result.output

    def test_the_drill_refuses_when_the_volume_is_not_ready(self, unreachable: None) -> None:
        result = _preflight("--drill")
        assert result.exit_code == 1
        assert "Cannot run the drill" in result.output


class TestReachableTarget:
    def test_each_remote_check_is_reported(self, healthy: None) -> None:
        # The branch where storage, deploy and audit are actually consulted —
        # three more places where cli.py could drift away from its modules.
        result = _preflight()
        assert "Responds over SSH." in result.output
        assert "LUKS volume formatted and mounted." in result.output
        assert "Answering on the target's localhost." in result.output
        assert "Nothing exposed to the local network." in result.output

    def test_is_not_ready_until_a_backup_exists(self, healthy: None) -> None:
        # A machine that passes every check it can still holds the only copy.
        result = _preflight()
        assert result.exit_code == 1
        assert "off-site backup configured" in result.output
        assert "NOT READY" in result.output


def _parameters(func: Callable[..., object]) -> list[tuple[str, object]]:
    return [(p.name, p.default) for p in inspect.signature(func).parameters.values()]


class TestTheFakes:
    """A fake with the wrong signature is how this bug would come back unseen."""

    @pytest.mark.parametrize("fake", [_no_answer, _answers])
    def test_ssh_fakes_match_sshexec_run(self, fake: Callable[..., sshexec.SshResult]) -> None:
        assert _parameters(fake) == _parameters(sshexec.run)

    def test_config_fake_matches_load_config(self) -> None:
        assert _parameters(_load_config) == _parameters(config.load_config)


# --- Verification wiring ---------------------------------------------------
#
# `backup_verified` was hard-coded False until stage 2. What replaced it decides
# whether the tool tells you it is safe to import documents, so every way of
# answering "no" is worth pinning: no record, a failed one, an expired one, and
# one that cannot say when it ran.


def _configured(path: Path | None = None) -> config.Config:
    """A host with an off-site repository configured."""
    return config.Config(
        host=config.HostConfig(address="nowhere.invalid", user="deploy"),
        backup=config.BackupConfig(restic_repository="/mnt/backup/restic"),
    )


def _record(performed_at: str, passed: bool = True) -> str:
    return json.dumps(
        {
            "level": "content",
            "performed_at": performed_at,
            "snapshot_id": "abcd1234",
            "repository_kind": "local",
            "passed": passed,
            "documents_expected": 12,
            "documents_found": 12,
            "sample_size": 5,
            "mismatches": [],
            "detail": "Restored 5 of 5 sampled files.",
        }
    )


def _answering_with(record: str | None) -> Callable[..., sshexec.SshResult]:
    def answer(
        destination: list[str],
        remote_command: str,
        timeout: int = 60,
        input_text: str | None = None,
    ) -> sshexec.SshResult:
        if record is not None and "verification.json" in remote_command:
            return sshexec.SshResult(exit_code=0, stdout=record, stderr="")
        if record is None and "verification.json" in remote_command:
            return sshexec.SshResult(exit_code=1, stdout="", stderr="")
        return _answers(destination, remote_command, timeout, input_text)

    return answer


def _flat(result: Result) -> str:
    """Output with wrapping collapsed.

    `rich` wraps to terminal width, so any assertion on a phrase longer than a
    few words is really an assertion about where the line broke.
    """
    return " ".join(result.output.split())


def _hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def with_verification(monkeypatch: pytest.MonkeyPatch) -> Callable[[str | None], None]:
    def apply(record: str | None) -> None:
        monkeypatch.setattr(config, "load_config", _configured)
        monkeypatch.setattr(sshexec, "run", _answering_with(record))

    return apply


class TestRestoreVerified:
    def test_a_recent_passing_record_gives_a_green_light(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        with_verification(_record(_hours_ago(1)))
        result = _preflight()
        assert "A restore has been performed and the documents came back." in _flat(result)
        assert result.exit_code == 0  # nothing blocking left

        # This configuration uses a *local* repository, which `pless audit`
        # warns about — so this also proves a warning does not withhold a green
        # light. One used to: "the volume is not mounted", which is the normal
        # state after a reboot and something preflight checks for itself.
        assert "exposure audit" in _flat(result)

    def test_no_record_is_not_verified(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        with_verification(None)
        result = _preflight()
        assert result.exit_code == 1
        assert "A backup that has not been restored is a belief" in _flat(result)

    def test_a_failed_record_is_not_verified(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        with_verification(_record(_hours_ago(1), passed=False))
        result = _preflight()
        assert result.exit_code == 1

    def test_an_expired_record_is_not_verified(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        # 15 days against a 14-day window: a restore proved long enough ago
        # says little about a repository written to every day since.
        with_verification(_record(_hours_ago(24 * 15)))
        result = _preflight()
        assert result.exit_code == 1

    def test_a_record_that_cannot_say_when_is_not_verified(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        with_verification(_record(""))
        result = _preflight()
        assert result.exit_code == 1

    def test_unreadable_json_does_not_crash_preflight(
        self, with_verification: Callable[[str | None], None]
    ) -> None:
        # Preflight must still deliver a verdict when the record is corrupt.
        with_verification("{not json")
        result = _preflight()
        assert result.exit_code == 1
