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
from collections.abc import Callable
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
