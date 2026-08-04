"""Tailscale på target: installasjon, innmelding og status.

Tailnet velges av auth-nøkkelen (TS_AUTHKEY) — én nøkkel hører til ett tailnet.
Selvhostet kontrollplan (Headscale) støttes via login_server.

Etter at Tailscale er oppe, kan `pless harden` stenge SSH mot LAN-et slik at
boksen ikke har én eneste åpen port for noen på det lokale nettet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pless import config, sshexec
from pless.targets import TargetHost

INTERFACE = "tailscale0"
INSTALL_URL = "https://tailscale.com/install.sh"


class TailscaleError(RuntimeError):
    pass


@dataclass
class TailscaleStatus:
    installed: bool
    backend_state: str
    hostname: str
    addresses: list[str]

    @property
    def is_up(self) -> bool:
        return self.backend_state == "Running" and bool(self.addresses)


def parse_status(status_json: str) -> TailscaleStatus:
    """Tolk `tailscale status --json`. Ren funksjon."""
    try:
        data = json.loads(status_json)
    except json.JSONDecodeError as exc:
        raise TailscaleError(f"Kunne ikke tolke tailscale-status: {exc}") from exc
    self_node = data.get("Self") or {}
    return TailscaleStatus(
        installed=True,
        backend_state=data.get("BackendState", "ukjent"),
        hostname=(self_node.get("DNSName") or "").rstrip("."),
        addresses=list(self_node.get("TailscaleIPs") or []),
    )


def _run(target: TargetHost, command: str, timeout: int = 120) -> sshexec.SshResult:
    return sshexec.run(
        target.user, target.host, target.key, command, timeout=timeout, port=target.port
    )


def status(target: TargetHost) -> TailscaleStatus:
    probe = _run(target, "command -v tailscale >/dev/null && tailscale status --json || echo ''")
    if not probe.stdout.strip():
        return TailscaleStatus(
            installed=False, backend_state="ikke installert", hostname="", addresses=[]
        )
    return parse_status(probe.stdout)


def install(target: TargetHost) -> None:
    """Installer Tailscale via deres offisielle script (legger til apt-repoet deres)."""
    result = _run(target, f"curl -fsSL {INSTALL_URL} | sudo sh", timeout=600)
    if not result.ok:
        raise TailscaleError(f"Installasjon feilet: {result.stderr.strip()}")


def up(
    cfg: config.Config,
    target: TargetHost,
    authkey: str,
    hostname: str = "",
) -> TailscaleStatus:
    """Meld boksen inn i tailnetet. Auth-nøkkelen går på stdin, aldri i argv."""
    if not authkey:
        raise TailscaleError("TS_AUTHKEY er ikke satt — lag en nøkkel i Tailscale admin → Keys.")

    args = ["--ssh=false", "--accept-dns=false"]
    if hostname:
        args.append(f"--hostname={hostname}")
    if cfg.tailscale.login_server:
        args.append(f"--login-server={cfg.tailscale.login_server}")

    # Nøkkelen leses fra stdin av shellet på targetet, så den havner ikke i
    # prosesslista (der enhver lokal bruker kunne sett den med `ps`).
    command = f'read -r KEY; sudo tailscale up --authkey="$KEY" {" ".join(args)}'
    result = _run(target, command, timeout=180)
    if not result.ok:
        raise TailscaleError(f"`tailscale up` feilet: {result.stderr.strip()}")
    return status(target)


# UFW-regler som flytter SSH fra «hele LAN-et» til «kun tailnetet».
# Rekkefølgen er kritisk: åpne på tailscale0 FØR den brede regelen fjernes.
HARDEN_SCRIPT = f"""\
set -eu
ufw allow in on {INTERFACE} to any port 22 proto tcp
ufw --force delete allow OpenSSH 2>/dev/null || true
ufw --force delete allow 22/tcp 2>/dev/null || true
ufw --force delete allow 22 2>/dev/null || true
ufw reload
"""


def harden(target: TargetHost) -> None:
    """Steng SSH mot LAN. Krever at Tailscale allerede er oppe — ellers låser vi oss ute."""
    current = status(target)
    if not current.is_up:
        raise TailscaleError(
            f"Tailscale er ikke oppe (state: {current.backend_state}). "
            "Nekter å stenge LAN-SSH før det finnes en annen vei inn — "
            "kjør `pless tailscale up` først."
        )
    result = sshexec.run(
        target.user,
        target.host,
        target.key,
        "sudo sh -s",
        timeout=120,
        input_text=HARDEN_SCRIPT,
        port=target.port,
    )
    if not result.ok:
        raise TailscaleError(f"Brannmur-innstramming feilet: {result.stderr.strip()}")
