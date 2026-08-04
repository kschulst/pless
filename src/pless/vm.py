"""Development VM backends. Thin wrappers around subprocess.

Two backends, each mirroring a production path:
  lima      -> Debian 13, provisioned by `pless bootstrap` (like a Pi)
  multipass -> Ubuntu, provisioned by cloud-init (like Hetzner)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pless import config

# The current Ubuntu LTS. Older releases are still supported by bootstrap —
# see SUPPORTED_DISTROS — but we test against what we recommend.
UBUNTU_IMAGE = "26.04"
LIMA_TEMPLATE = "template://debian-13"

BACKENDS = ("lima", "multipass")


class VmError(RuntimeError):
    pass


def require_backend(backend: str) -> None:
    if backend not in BACKENDS:
        raise VmError(f"Unknown vm.backend {backend!r} (valid: {', '.join(BACKENDS)}).")
    if backend == "lima" and shutil.which("limactl") is None:
        raise VmError("lima is not installed. Run `brew install lima`.")
    if backend == "multipass" and shutil.which("multipass") is None:
        raise VmError("multipass is not installed. Run `brew install --cask multipass`.")


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
    """Create the VM, blocking until it is up. First run downloads an image.

    Lima gets no cloud-init: it is provisioned by `pless bootstrap` over SSH,
    exactly as a Pi is. Multipass gets the host spec as cloud-init, as a cloud
    server does.
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
            raise VmError(f"limactl start failed: {completed.stderr.strip()}")
        return

    if user_data_path is None:
        raise VmError("The multipass backend requires cloud-init user-data.")
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
        raise VmError(f"multipass launch failed: {completed.stderr.strip()}")


def info(cfg: config.VmConfig) -> dict:
    """Normalised status: {'state': str, 'addresses': list[str]}."""
    if cfg.backend == "lima":
        completed = _lima(["list", "--format", "json"], timeout=30)
        for line in completed.stdout.strip().splitlines():
            data = json.loads(line)
            if data.get("name") == cfg.name:
                port = data.get("sshLocalPort")
                return {
                    "state": data.get("status", "unknown"),
                    "addresses": [f"127.0.0.1:{port}"] if port else [],
                }
        raise VmError(f"Lima does not know a VM named {cfg.name!r}.")

    completed = _run(["info", cfg.name, "--format", "json"], timeout=30)
    if completed.returncode != 0:
        raise VmError(f"multipass info failed: {completed.stderr.strip()}")
    data = json.loads(completed.stdout)["info"][cfg.name]
    return {"state": data.get("state", "unknown"), "addresses": data.get("ipv4") or []}


def delete(cfg: config.VmConfig) -> None:
    if cfg.backend == "lima":
        stop = _lima(["stop", "--force", cfg.name], timeout=120)
        if stop.returncode != 0 and "not running" not in stop.stderr.lower():
            raise VmError(f"limactl stop failed: {stop.stderr.strip()}")
        completed = _lima(["delete", "--force", cfg.name], timeout=120)
        if completed.returncode != 0:
            raise VmError(f"limactl delete failed: {completed.stderr.strip()}")
        return
    completed = _run(["delete", "--purge", cfg.name], timeout=120)
    if completed.returncode != 0:
        raise VmError(f"multipass delete failed: {completed.stderr.strip()}")
