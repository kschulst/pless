"""Tailscale on the target: install, join, status.

Which tailnet the machine joins is decided by the auth key (TS_AUTHKEY) — one
key belongs to one tailnet. A self-hosted control plane (Headscale) is
supported through login_server.

Once Tailscale is up, `pless harden` can close SSH to the LAN so the machine
has no open port at all for anyone on the local network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pless import config, sshexec
from pless.targets import Host

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
    """Parse `tailscale status --json`. Pure function."""
    try:
        data = json.loads(status_json)
    except json.JSONDecodeError as exc:
        raise TailscaleError(f"Could not parse Tailscale status: {exc}") from exc
    self_node = data.get("Self") or {}
    return TailscaleStatus(
        installed=True,
        backend_state=data.get("BackendState", "ukjent"),
        hostname=(self_node.get("DNSName") or "").rstrip("."),
        addresses=list(self_node.get("TailscaleIPs") or []),
    )


def _run(target: Host, command: str, timeout: int = 120) -> sshexec.SshResult:
    return sshexec.run(target.ssh_args, command, timeout=timeout)


def status(target: Host) -> TailscaleStatus:
    probe = _run(target, "command -v tailscale >/dev/null && tailscale status --json || echo ''")
    if not probe.stdout.strip():
        return TailscaleStatus(
            installed=False, backend_state="not installed", hostname="", addresses=[]
        )
    return parse_status(probe.stdout)


def install(target: Host) -> None:
    """Install Tailscale using their official script, which adds their apt repo."""
    result = _run(target, f"curl -fsSL {INSTALL_URL} | sudo sh", timeout=600)
    if not result.ok:
        raise TailscaleError(f"Installation failed: {result.stderr.strip()}")


def up(
    cfg: config.Config,
    target: Host,
    authkey: str,
    hostname: str = "",
) -> TailscaleStatus:
    """Join the tailnet. The auth key goes on stdin, never in argv."""
    if not authkey:
        raise TailscaleError(
            "TS_AUTHKEY is not set — create one in the Tailscale admin console under Keys."
        )

    args = ["--ssh=false", "--accept-dns=false"]
    if hostname:
        args.append(f"--hostname={hostname}")
    if cfg.tailscale.login_server:
        args.append(f"--login-server={cfg.tailscale.login_server}")

    # The shell on the target reads the key from stdin, so it never appears in
    # the process list where any local user could read it with `ps`.
    command = f'read -r KEY; sudo tailscale up --authkey="$KEY" {" ".join(args)}'
    result = _run(target, command, timeout=180)
    if not result.ok:
        raise TailscaleError(f"`tailscale up` failed: {result.stderr.strip()}")
    return status(target)


# UFW rules moving SSH from "the whole LAN" to "the tailnet only".
# The order is critical: open on tailscale0 BEFORE removing the broad rule.
HARDEN_SCRIPT = f"""\
set -eu
ufw allow in on {INTERFACE} to any port 22 proto tcp
ufw --force delete allow OpenSSH 2>/dev/null || true
ufw --force delete allow 22/tcp 2>/dev/null || true
ufw --force delete allow 22 2>/dev/null || true
ufw reload
"""


def harden(target: Host) -> None:
    """Close SSH to the LAN. Requires Tailscale to be up, or we lock ourselves out."""
    current = status(target)
    if not current.is_up:
        raise TailscaleError(
            f"Tailscale is not up (state: {current.backend_state}). "
            "Refusing to close LAN SSH before another way in exists — "
            "run `pless tailscale up` first."
        )
    result = sshexec.run(target.ssh_args, "sudo sh -s", timeout=120, input_text=HARDEN_SCRIPT)
    if not result.ok:
        raise TailscaleError(f"Firewall hardening failed: {result.stderr.strip()}")
