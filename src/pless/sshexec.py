"""SSH via subprocess and the system ssh binary.

Chosen over paramiko/fabric because the system ssh already reuses ssh-agent,
~/.ssh/config, known_hosts and any ProxyJump or multiplexing setup, none of
which we then have to reimplement. Boring and reliable.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SshResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def ssh_command(user: str, host: str, key: Path, remote_command: str, port: int = 22) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(port),
        "-i",
        str(key),
        f"{user}@{host}",
        remote_command,
    ]


def run(
    user: str,
    host: str,
    key: Path,
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
    port: int = 22,
) -> SshResult:
    """Run a command over SSH.

    `input_text` is sent on stdin, which is how secrets are passed: never in
    argv, where any local user could read them with `ps`.
    """
    completed = subprocess.run(
        ssh_command(user, host, key, remote_command, port),
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
