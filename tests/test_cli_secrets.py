"""The output contract of `pless secrets generate`.

The command exists to be piped into a password manager's CLI, so *what lands on
stdout* is the contract: exactly one secret, one line, nothing else. The
reminder belongs on stderr, where a pipe does not carry it.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pless import cli, secretgen

runner = CliRunner()


def stdout_of(result) -> str:
    return result.stdout


class TestSecretsGenerate:
    def test_stdout_is_one_line_and_nothing_else(self) -> None:
        result = runner.invoke(cli.app, ["secrets", "generate", "--quiet"])
        assert result.exit_code == 0
        assert len(stdout_of(result).strip().splitlines()) == 1

    def test_the_default_is_the_human_format(self) -> None:
        result = runner.invoke(cli.app, ["secrets", "generate", "--quiet"])
        value = stdout_of(result).strip()
        groups = value.split("-")
        assert len(groups) == 5
        assert all(len(g) == 5 for g in groups)
        assert set(value.replace("-", "")) <= set(secretgen.CROCKFORD_ALPHABET)

    def test_the_machine_format_is_a_base64url_token(self) -> None:
        result = runner.invoke(cli.app, ["secrets", "generate", "--kind", "machine", "--quiet"])
        value = stdout_of(result).strip()
        assert len(value) == 43
        assert "-----" not in value

    def test_quiet_suppresses_the_reminder(self) -> None:
        # Matched on a short phrase: rich wraps the message to terminal width,
        # so anything longer can be split across a line break.
        noisy = runner.invoke(cli.app, ["secrets", "generate"])
        quiet = runner.invoke(cli.app, ["secrets", "generate", "--quiet"])
        assert "Save it now" in noisy.stderr
        assert "Save it now" not in quiet.stderr

    def test_the_reminder_never_lands_on_stdout(self) -> None:
        # If it did, piping into a vault CLI would store the reminder as part of
        # the secret, and the operator would not notice until they needed it.
        result = runner.invoke(cli.app, ["secrets", "generate"])
        assert len(stdout_of(result).strip().splitlines()) == 1

    def test_an_unknown_kind_is_refused(self) -> None:
        result = runner.invoke(cli.app, ["secrets", "generate", "--kind", "medium"])
        assert result.exit_code != 0

    def test_every_invocation_differs(self) -> None:
        values = {
            stdout_of(runner.invoke(cli.app, ["secrets", "generate", "--quiet"])).strip()
            for _ in range(20)
        }
        assert len(values) == 20

    @pytest.mark.parametrize("kind", ["human", "machine"])
    def test_both_kinds_succeed(self, kind: str) -> None:
        result = runner.invoke(cli.app, ["secrets", "generate", "--kind", kind, "--quiet"])
        assert result.exit_code == 0
        assert stdout_of(result).strip()
