"""Deploying the Paperless stack to a target: files, systemd unit, start, health."""

from __future__ import annotations

import shlex
import time

from pless import composegen, config, sshexec, storage
from pless.targets import TargetHost

DATA_SUBDIRS = ["consume", "export", "media", "data", "postgres", "redis", "backups"]


class DeployError(RuntimeError):
    pass


def _run(
    target: TargetHost, command: str, input_text: str | None = None, timeout: int = 120
) -> sshexec.SshResult:
    return sshexec.run(
        target.user,
        target.host,
        target.key,
        command,
        timeout=timeout,
        input_text=input_text,
        port=target.port,
    )


def _run_ok(
    target: TargetHost, command: str, input_text: str | None = None, timeout: int = 120
) -> str:
    result = _run(target, command, input_text, timeout)
    if not result.ok:
        raise DeployError(
            f"Remote command failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _write_remote_file(
    target: TargetHost, path: str, content: str, mode: str = "0644", owner: str | None = None
) -> None:
    """Write a file via stdin, so its contents never reach argv or a temp file."""
    quoted = shlex.quote(path)
    _run_ok(target, f"sudo tee {quoted} > /dev/null && sudo chmod {mode} {quoted}", content)
    if owner:
        _run_ok(target, f"sudo chown {owner} {quoted}")


def install(cfg: config.Config, secrets: config.Secrets, target: TargetHost) -> None:
    """Write the compose file, server-side .env and systemd unit, then start."""
    state = storage.status(cfg, target)
    if not state.is_mounted:
        raise DeployError(
            f"{storage.MOUNTPOINT} is not mounted — run `pless unlock` first. "
            "The stack must never be deployed outside the encrypted volume."
        )

    subdirs = " ".join(f"{composegen.INSTALL_DIR}/{d}" for d in DATA_SUBDIRS)
    _run_ok(target, f"sudo mkdir -p {subdirs}")
    # UID/GID 1000 matches USERMAP in the compose file; postgres owns its own directory.
    _run_ok(
        target,
        f"sudo chown -R 1000:1000 {composegen.INSTALL_DIR}/consume "
        f"{composegen.INSTALL_DIR}/export {composegen.INSTALL_DIR}/media "
        f"{composegen.INSTALL_DIR}/data",
    )

    _write_remote_file(
        target, f"{composegen.INSTALL_DIR}/docker-compose.yml", composegen.render_compose(cfg)
    )
    _write_remote_file(
        target,
        f"{composegen.INSTALL_DIR}/.env",
        composegen.render_server_env(secrets),
        mode="0600",
        owner="root:root",
    )
    _write_remote_file(target, "/etc/systemd/system/paperless.service", composegen.SYSTEMD_UNIT)
    _run_ok(target, "sudo systemctl daemon-reload")
    # The first start pulls roughly 2 GB of images — allow plenty of time.
    _run_ok(target, "sudo systemctl start paperless.service", timeout=900)


def compose(target: TargetHost, args: str, timeout: int = 120) -> str:
    return _run_ok(
        target, f"cd {composegen.INSTALL_DIR} && sudo docker compose {args}", timeout=timeout
    )


def http_status(target: TargetHost) -> str:
    """HTTP status from Paperless on the target's localhost. '000' means no answer."""
    return _run_ok(
        target,
        f"curl -s -o /dev/null -w '%{{http_code}}' -m 5 http://127.0.0.1:{composegen.WEB_PORT} "
        "|| echo 000",
    )


def wait_healthy(target: TargetHost, timeout_seconds: int = 300) -> bool:
    """Wait for the web server to answer. The first start migrates the database."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if http_status(target) in ("200", "302"):
            return True
        time.sleep(10)
    return False
