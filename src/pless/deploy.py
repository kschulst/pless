"""Deploy av Paperless-stacken til target: filer, systemd-unit, oppstart, helse."""

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
        target.user, target.host, target.key, command, timeout=timeout, input_text=input_text
    )


def _run_ok(
    target: TargetHost, command: str, input_text: str | None = None, timeout: int = 120
) -> str:
    result = _run(target, command, input_text, timeout)
    if not result.ok:
        raise DeployError(f"Fjernkommando feilet: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _write_remote_file(
    target: TargetHost, path: str, content: str, mode: str = "0644", owner: str | None = None
) -> None:
    """Skriv fil via stdin — innholdet (inkl. secrets) rører aldri argv eller temp-filer."""
    quoted = shlex.quote(path)
    _run_ok(target, f"sudo tee {quoted} > /dev/null && sudo chmod {mode} {quoted}", content)
    if owner:
        _run_ok(target, f"sudo chown {owner} {quoted}")


def install(cfg: config.Config, secrets: config.Secrets, target: TargetHost) -> None:
    """Legg ut compose-fil, server-.env og systemd-unit, og start stacken."""
    state = storage.status(cfg, target)
    if not state.is_mounted:
        raise DeployError(
            f"{storage.MOUNTPOINT} er ikke montert — kjør `pless unlock` først. "
            "Stacken skal aldri deployes utenfor den krypterte disken."
        )

    subdirs = " ".join(f"{composegen.INSTALL_DIR}/{d}" for d in DATA_SUBDIRS)
    _run_ok(target, f"sudo mkdir -p {subdirs}")
    # UID/GID 1000 matcher USERMAP i compose; postgres-containeren eier sin egen mappe.
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
    # Første oppstart puller images (~2 GB) — romslig timeout.
    _run_ok(target, "sudo systemctl start paperless.service", timeout=900)


def compose(target: TargetHost, args: str, timeout: int = 120) -> str:
    return _run_ok(
        target, f"cd {composegen.INSTALL_DIR} && sudo docker compose {args}", timeout=timeout
    )


def http_status(target: TargetHost) -> str:
    """HTTP-status fra Paperless på targetets localhost. '000' = ingen respons."""
    return _run_ok(
        target,
        f"curl -s -o /dev/null -w '%{{http_code}}' -m 5 http://127.0.0.1:{composegen.WEB_PORT} "
        "|| echo 000",
    )


def wait_healthy(target: TargetHost, timeout_seconds: int = 300) -> bool:
    """Vent til webserveren svarer. Første oppstart migrerer databasen — tar tid."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        code = http_status(target)
        if code in ("200", "302"):
            return True
        time.sleep(10)
    return False
