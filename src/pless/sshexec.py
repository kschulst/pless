"""SSH via subprocess and the system ssh binary.

Chosen over paramiko/fabric because the system ssh already reuses ssh-agent,
~/.ssh/config, known_hosts and any ProxyJump or multiplexing setup, none of
which we then have to reimplement. Boring and reliable — and it is why a host
can be described by an SSH config file rather than by a target type.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# Applied to every connection. Command-line options win over anything in an
# SSH config file, so these hold even when the destination comes from one.
COMMON_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=10",
]


# What `timeout(1)` exits with, so the number is recognisable rather than
# invented. `ssh` itself uses 255 for its own failures and passes the remote
# command's status through otherwise, so this cannot collide with either.
TIMEOUT_EXIT_CODE = 124


@dataclass
class SshResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def ssh_command(destination: list[str], remote_command: str | None = None) -> list[str]:
    """Build an ssh invocation.

    `destination` is everything identifying the machine — either
    `["-p", "22", "-i", key, "user@address"]` or `["-F", config, "alias"]`.
    """
    command = ["ssh", *COMMON_OPTIONS, *destination]
    if remote_command is not None:
        command.append(remote_command)
    return command


def _as_text(value: str | bytes | None) -> str:
    """Whatever partial output a timeout left behind, as a string.

    `TimeoutExpired.stdout` is populated inconsistently — bytes on some paths,
    text on others, absent on POSIX where `run` does not re-read it — and
    partial output is often the most diagnostic part of a hang.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run(
    destination: list[str],
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
) -> SshResult:
    """Run a command over SSH.

    `input_text` is sent on stdin, which is how secrets are passed: never in
    argv, where any local user could read them with `ps`.
    """
    try:
        completed = subprocess.run(
            ssh_command(destination, remote_command),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as expired:
        # A target that accepts the connection and then stops answering is the
        # realistic case — memory pressure, stuck I/O — and letting this
        # propagate turned every command into a traceback at the moment the
        # machine was least able to answer for itself. `ConnectTimeout` in
        # COMMON_OPTIONS bounds the handshake, not the remote command.
        return SshResult(
            exit_code=TIMEOUT_EXIT_CODE,
            stdout=_as_text(expired.stdout),
            stderr=(
                f"Timed out after {timeout}s. The connection was accepted but the command "
                "did not return, which is what a machine under memory pressure or with "
                "stuck I/O does. Nothing was confirmed either way."
            ),
            timed_out=True,
        )
    return SshResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
