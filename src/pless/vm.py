"""Dev-VM-backends (beslutning #21, #30). Tynne, subprocess-baserte.

To backends, som dekker hver sin produksjonsløype:
  lima      → Debian 13, provisjoneres med `pless bootstrap` (speiler Pi/RPi OS)
  multipass → Ubuntu, provisjoneres med cloud-init (speiler Hetzner)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pless import config

UBUNTU_IMAGE = "24.04"
LIMA_TEMPLATE = "template://debian-13"

BACKENDS = ("lima", "multipass")


class VmError(RuntimeError):
    pass


def require_backend(backend: str) -> None:
    if backend not in BACKENDS:
        raise VmError(f"Ukjent vm.backend {backend!r} (gyldig: {', '.join(BACKENDS)}).")
    if backend == "lima" and shutil.which("limactl") is None:
        raise VmError("lima er ikke installert. Kjør `brew install lima`.")
    if backend == "multipass" and shutil.which("multipass") is None:
        raise VmError(
            "multipass er ikke installert. Kjør `! brew install --cask multipass --yes` "
            "(krever sudo-passord interaktivt)."
        )


def require_multipass() -> None:
    require_backend("multipass")


def _run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["multipass", *args], capture_output=True, text=True, timeout=timeout)


def _lima(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["limactl", *args], capture_output=True, text=True, timeout=timeout)


def exists(cfg: config.VmConfig) -> bool:
    if cfg.backend == "lima":
        completed = _lima(["list", "--format", "json"], timeout=30)
        return any(
            json.loads(line).get("name") == cfg.name
            for line in completed.stdout.strip().splitlines()
            if line.strip()
        )
    return _run(["info", cfg.name, "--format", "json"], timeout=30).returncode == 0


def launch(cfg: config.VmConfig, user_data_path: Path | None = None) -> None:
    """Opprett VM-en. Blokkerer til den er oppe — første gang lastes imaget ned.

    lima får ingen cloud-init: den provisjoneres av `pless bootstrap` over SSH,
    nøyaktig som Pi-en. multipass får host-spec som cloud-init, som Hetzner.
    """
    if cfg.backend == "lima":
        completed = _lima(
            [
                "start",
                LIMA_TEMPLATE,
                f"--name={cfg.name}",
                f"--cpus={cfg.cpus}",
                f"--memory={cfg.memory_gb}",
                f"--disk={cfg.disk_gb}",
                "--tty=false",
            ]
        )
        if completed.returncode != 0:
            raise VmError(f"limactl start feilet: {completed.stderr.strip()}")
        return

    if user_data_path is None:
        raise VmError("multipass-backenden krever cloud-init user-data.")
    completed = _run(
        [
            "launch",
            UBUNTU_IMAGE,
            "--name",
            cfg.name,
            "--cpus",
            str(cfg.cpus),
            "--memory",
            cfg.memory,
            "--disk",
            cfg.disk,
            "--cloud-init",
            str(user_data_path),
        ]
    )
    if completed.returncode != 0:
        raise VmError(f"multipass launch feilet: {completed.stderr.strip()}")


def info(cfg: config.VmConfig) -> dict:
    """Normalisert status: {'state': str, 'addresses': list[str]}."""
    if cfg.backend == "lima":
        completed = _lima(["list", "--format", "json"], timeout=30)
        for line in completed.stdout.strip().splitlines():
            data = json.loads(line)
            if data.get("name") == cfg.name:
                port = data.get("sshLocalPort")
                return {
                    "state": data.get("status", "ukjent"),
                    "addresses": [f"127.0.0.1:{port}"] if port else [],
                }
        raise VmError(f"Lima kjenner ikke VM-en {cfg.name!r}.")

    completed = _run(["info", cfg.name, "--format", "json"], timeout=30)
    if completed.returncode != 0:
        raise VmError(f"multipass info feilet: {completed.stderr.strip()}")
    data = json.loads(completed.stdout)["info"][cfg.name]
    return {"state": data.get("state", "ukjent"), "addresses": data.get("ipv4") or []}


def delete(cfg: config.VmConfig) -> None:
    if cfg.backend == "lima":
        stop = _lima(["stop", "--force", cfg.name], timeout=120)
        if stop.returncode != 0 and "not running" not in stop.stderr.lower():
            raise VmError(f"limactl stop feilet: {stop.stderr.strip()}")
        completed = _lima(["delete", "--force", cfg.name], timeout=120)
        if completed.returncode != 0:
            raise VmError(f"limactl delete feilet: {completed.stderr.strip()}")
        return
    completed = _run(["delete", "--purge", cfg.name], timeout=120)
    if completed.returncode != 0:
        raise VmError(f"multipass delete feilet: {completed.stderr.strip()}")
