"""Preflight, driven through the CLI.

`preflight` is the command that decides whether an installation can be trusted
with documents, and it was calling `sshexec.run` with a signature that module
has not had for some time — so it raised TypeError before checking anything.
Every other module is covered by tests over pure functions, which is exactly why
the wiring in `cli.py` was where the drift hid.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pless import cli, sshexec, targets

runner = CliRunner()


@pytest.fixture
def unreachable_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves but does not answer."""
    monkeypatch.setattr(
        cli.targets,
        "resolve_host",
        lambda cfg: targets.Host(label="test-target", ssh_args=["-F", "/dev/null", "nowhere"]),
    )
    monkeypatch.setattr(
        cli.sshexec,
        "run",
        lambda *args, **kwargs: sshexec.SshResult(exit_code=255, stdout="", stderr="unreachable"),
    )


class TestPreflight:
    def test_runs_against_an_unreachable_target_without_crashing(
        self, unreachable_target: None
    ) -> None:
        result = runner.invoke(cli.app, ["preflight"])
        assert not isinstance(result.exception, TypeError), result.exception
        assert result.exit_code == 1  # blockers present, which is the point
        assert "target reachable" in result.output

    def test_says_what_is_wrong_rather_than_only_that_something_is(
        self, unreachable_target: None
    ) -> None:
        result = runner.invoke(cli.app, ["preflight"])
        assert "No response over SSH" in result.output
        assert "NOT READY" in result.output

    def test_still_reports_the_missing_backup_capabilities(self, unreachable_target: None) -> None:
        # Silence here would read as approval, so the absent capabilities are
        # listed explicitly even when the target cannot be reached at all.
        result = runner.invoke(cli.app, ["preflight"])
        assert "restore verified" in result.output

    def test_the_drill_refuses_when_the_volume_is_not_ready(self, unreachable_target: None) -> None:
        result = runner.invoke(cli.app, ["preflight", "--drill"])
        assert result.exit_code == 1
        assert "Cannot run the drill" in result.output
