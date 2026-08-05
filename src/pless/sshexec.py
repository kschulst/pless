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


@dataclass
class SshResult:
    exit_code: int
    stdout: str
    stderr: str

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
    completed = subprocess.run(
        ssh_command(destination, remote_command),
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )
    return SshResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
