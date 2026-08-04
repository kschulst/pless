"""Target-oppslag: alle targets ender som «en SSH-tilgjengelig host» (beslutning #11/#18)."""

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
    """Hent IPv4 fra `multipass info <name> --format json`. Ren funksjon, testbar."""
    data = json.loads(info_json)
    info = data.get("info", {}).get(name)
    if info is None:
        raise TargetError(f"Multipass kjenner ikke VM-en {name!r}.")
    if info.get("state") != "Running":
        raise TargetError(f"VM-en {name!r} er ikke Running (state: {info.get('state')}).")
    ipv4 = info.get("ipv4") or []
    if not ipv4:
        raise TargetError(f"VM-en {name!r} har ingen IPv4 ennå.")
    return ipv4[0]


def parse_lima_instance(list_json: str, name: str) -> tuple[str, int]:
    """Hent (bruker, ssh-port) fra `limactl list --format json`.

    Lima kjører VM-en bak port-forwarding på 127.0.0.1, ikke på egen IP.
    Utdata er én JSON-linje per instans.
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
            raise TargetError(
                f"Lima-VM-en {name!r} er ikke Running (status: {data.get('status')})."
            )
        port = data.get("sshLocalPort")
        if not port:
            raise TargetError(f"Lima-VM-en {name!r} har ingen SSH-port ennå.")
        return data.get("config", {}).get("user", {}).get("name") or getpass.getuser(), int(port)
    raise TargetError(f"Lima kjenner ikke VM-en {name!r}.")


def _lima_list() -> str:
    completed = subprocess.run(
        ["limactl", "list", "--format", "json"], capture_output=True, text=True
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise TargetError(f"`limactl list` feilet: {detail}")
    return completed.stdout


def lima_identity_file() -> Path:
    """Lima genererer sin egen nøkkel; den virker alltid mot lima-VM-er."""
    return Path.home() / ".lima" / "_config" / "user"


def _multipass_info(name: str) -> str:
    completed = subprocess.run(
        ["multipass", "info", name, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise TargetError(f"`multipass info {name}` feilet: {detail}")
    return completed.stdout


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
            raise TargetError("[pi] host er ikke satt i pless.toml.")
        return TargetHost(name="pi", user=cfg.pi.user, host=cfg.pi.host, key=cfg.ssh.key)
    if target_type == "hetzner":
        client = hetzner.make_client(secrets.hcloud_token)
        server = hetzner.get_server(client, cfg.hetzner.server_name)
        if server is None:
            raise TargetError(f"Fant ingen Hetzner-server ved navn {cfg.hetzner.server_name!r}.")
        return TargetHost(
            name=server.name,
            user=cfg.ssh.user,
            host=hetzner.server_ip(server),
            key=cfg.ssh.key,
        )
    raise TargetError(f"Ukjent target.type: {target_type!r} (gyldig: vm, pi, hetzner)")
