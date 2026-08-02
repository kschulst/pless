"""Multipass-wrapper for dev-VM-target (beslutning #21). Tynn, subprocess-basert."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pless import config

UBUNTU_IMAGE = "24.04"


class VmError(RuntimeError):
    pass


def require_multipass() -> None:
    if shutil.which("multipass") is None:
        raise VmError(
            "multipass er ikke installert. Kjør `! brew install --cask multipass --yes` "
            "(krever sudo-passord interaktivt)."
        )


def _run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["multipass", *args], capture_output=True, text=True, timeout=timeout)


def exists(name: str) -> bool:
    return _run(["info", name, "--format", "json"], timeout=30).returncode == 0


def launch(cfg: config.VmConfig, user_data_path: Path) -> None:
    """Opprett VM-en med cloud-init. Blokkerer til den er oppe (image-nedlasting
    første gang kan ta noen minutter)."""
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


def info(name: str) -> dict:
    completed = _run(["info", name, "--format", "json"], timeout=30)
    if completed.returncode != 0:
        raise VmError(f"multipass info feilet: {completed.stderr.strip()}")
    return json.loads(completed.stdout)["info"][name]


def delete(name: str) -> None:
    completed = _run(["delete", "--purge", name], timeout=120)
    if completed.returncode != 0:
        raise VmError(f"multipass delete feilet: {completed.stderr.strip()}")
