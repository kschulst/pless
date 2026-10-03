"""A stand-in for the SSH round trip, shared by the tests that need one.

Lifted out of `test_restore.py` when the mirror tests needed it too: two copies
of a double is how they drift from the thing they stand in for, which is the
failure this project keeps finding.
"""

from __future__ import annotations

from pless import sshexec


class FakeSsh:
    """Answers remote commands by substring, and remembers the order they came in."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.commands: list[str] = []

    def run(
        self,
        destination: list[str],
        remote_command: str,
        timeout: int = 60,
        input_text: str | None = None,
    ) -> sshexec.SshResult:
        self.commands.append(remote_command)
        for fragment, (code, out) in self.answers.items():
            if fragment in remote_command:
                return sshexec.SshResult(exit_code=code, stdout=out, stderr="" if not code else out)
        return sshexec.SshResult(exit_code=0, stdout="", stderr="")

    def ran(self, fragment: str) -> bool:
        return any(fragment in command for command in self.commands)

    def index_of(self, fragment: str) -> int:
        for position, command in enumerate(self.commands):
            if fragment in command:
                return position
        raise AssertionError(f"No command contained {fragment!r}: {self.commands}")
