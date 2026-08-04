"""Target lookup: every target ends up as "a host reachable over SSH"."""

from __future__ import annotations

import getpass
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pless import config, hetzner


class TargetError(RuntimeError):
    pass


@dataclass
class TargetHost:
    name: str
    user: str
    host: str
    key: Path
    port: int = 22


def parse_multipass_ip(info_json: str, name: str) -> str:
    """Extract the IPv4 address from `multipass info <name> --format json`."""
    data = json.loads(info_json)
    info = data.get("info", {}).get(name)
    if info is None:
        raise TargetError(f"Multipass does not know a VM named {name!r}.")
    if info.get("state") != "Running":
        raise TargetError(f"VM {name!r} is not Running (state: {info.get('state')}).")
    ipv4 = info.get("ipv4") or []
    if not ipv4:
        raise TargetError(f"VM {name!r} has no IPv4 address yet.")
    return ipv4[0]


def parse_lima_instance(list_json: str, name: str) -> tuple[str, int]:
    """Extract (user, ssh port) from `limactl list --format json`.

    Lima runs its VMs behind port forwarding on 127.0.0.1 rather than giving
    them an address of their own. Output is one JSON object per line.
    """
    for line in list_json.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("name") != name:
            continue
        if data.get("status") != "Running":
            raise TargetError(f"Lima VM {name!r} is not Running (status: {data.get('status')}).")
        port = data.get("sshLocalPort")
        if not port:
            raise TargetError(f"Lima VM {name!r} has no SSH port yet.")
        return data.get("config", {}).get("user", {}).get("name") or getpass.getuser(), int(port)
    raise TargetError(f"Lima does not know a VM named {name!r}.")


_INSTALL_HINTS = {
    "limactl": "Install it with `brew install lima`.",
    "multipass": "Install it with `brew install --cask multipass`.",
}


def _run_tool(args: list[str]) -> str:
    """Run an external tool, explaining rather than crashing when it is missing."""
    tool = args[0]
    try:
        completed = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        hint = _INSTALL_HINTS.get(tool, "")
        raise TargetError(f"{tool} was not found in PATH. {hint}".strip()) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise TargetError(f"`{' '.join(args)}` failed: {detail}")
    return completed.stdout


def _lima_list() -> str:
    return _run_tool(["limactl", "list", "--format", "json"])


def lima_identity_file() -> Path:
    """Lima generates its own key, which always works against Lima VMs."""
    return Path.home() / ".lima" / "_config" / "user"


def _multipass_info(name: str) -> str:
    return _run_tool(["multipass", "info", name, "--format", "json"])


def resolve_target(cfg: config.Config, secrets: config.Secrets) -> TargetHost:
    target_type = cfg.target.type
    if target_type == "vm":
        if cfg.vm.backend == "lima":
            user, port = parse_lima_instance(_lima_list(), cfg.vm.name)
            return TargetHost(
                name=cfg.vm.name,
                user=user,
                host="127.0.0.1",
                key=lima_identity_file(),
                port=port,
            )
        ip = parse_multipass_ip(_multipass_info(cfg.vm.name), cfg.vm.name)
        return TargetHost(name=cfg.vm.name, user="ubuntu", host=ip, key=cfg.ssh.key)
    if target_type == "pi":
        if not cfg.pi.host:
            raise TargetError("[pi] host is not set in pless.toml.")
        return TargetHost(name="pi", user=cfg.pi.user, host=cfg.pi.host, key=cfg.ssh.key)
    if target_type == "hetzner":
        client = hetzner.make_client(secrets.hcloud_token)
        server = hetzner.get_server(client, cfg.hetzner.server_name)
        if server is None:
            raise TargetError(f"No Hetzner server named {cfg.hetzner.server_name!r} was found.")
        return TargetHost(
            name=server.name,
            user=cfg.ssh.user,
            host=hetzner.server_ip(server),
            key=cfg.ssh.key,
        )
    raise TargetError(f"Unknown target.type: {target_type!r} (valid: vm, pi, hetzner)")
