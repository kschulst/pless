"""Felles host-spec: hva enhver pless-server skal inneholde, som cloud-init user-data.

Samme spec brukes av alle targets som kan cloud-init (Multipass-VM, Ubuntu Server
på Pi via boot-partisjonen, Hetzner). Targets uten cloud-init får senere en
SSH-bootstrap som applikerer samme spec (beslutning #18).
"""

from __future__ import annotations

from pathlib import Path

import yaml

SSHD_HARDENING = """\
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
"""

# Beslutning #16: full auto-patching inkl. reboot. Reboot-tidspunkt kl 04:30
# lokal tid; etter reboot står LUKS låst til `pless unlock` (varsling kommer).
UNATTENDED_AUTO_REBOOT = """\
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
"""

PACKAGES = [
    "docker.io",
    "docker-compose-v2",
    "ufw",
    "fail2ban",
    "unattended-upgrades",
    "cryptsetup-bin",
    "curl",
]


def read_pubkey(private_key_path: Path) -> str:
    pub_path = Path(str(private_key_path) + ".pub")
    if not pub_path.is_file():
        raise FileNotFoundError(f"Fant ikke offentlig nøkkel: {pub_path}")
    return pub_path.read_text().strip()


def build_user_data(ssh_pubkey: str, timezone: str, admin_user: str = "ubuntu") -> dict:
    return {
        "timezone": timezone,
        "package_update": True,
        "package_upgrade": True,
        "packages": list(PACKAGES),
        "ssh_authorized_keys": [ssh_pubkey],
        "write_files": [
            {
                "path": "/etc/ssh/sshd_config.d/60-pless.conf",
                "content": SSHD_HARDENING,
                "permissions": "0644",
            },
            {
                "path": "/etc/apt/apt.conf.d/52pless-auto-reboot",
                "content": UNATTENDED_AUTO_REBOOT,
                "permissions": "0644",
            },
        ],
        "runcmd": [
            f"usermod -aG docker {admin_user}",
            "ufw allow OpenSSH",
            "ufw --force enable",
            "systemctl enable --now fail2ban",
            "systemctl enable --now unattended-upgrades",
            "systemctl restart ssh",
        ],
    }


def render_user_data(ssh_pubkey: str, timezone: str, admin_user: str = "ubuntu") -> str:
    spec = build_user_data(ssh_pubkey, timezone, admin_user)
    return "#cloud-config\n" + yaml.safe_dump(spec, sort_keys=False, width=120)


def render_bootstrap_script(timezone: str, admin_user: str = "ubuntu") -> str:
    """Samme spec som cloud-init, men som idempotent shell-script over SSH.

    Brukes når targetet allerede er booted og vi aldri rørte boot-partisjonen —
    typisk Pi etter Network Install (beslutning #18: felles spec, adapter per target).
    authorized_keys røres bevisst ikke: kommer vi inn over SSH, virker nøkkelen alt.
    """
    packages = " ".join(PACKAGES)
    return f"""#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive

timedatectl set-timezone {timezone}

apt-get update -qq
apt-get install -y -qq --no-install-recommends {packages}

cat > /etc/ssh/sshd_config.d/60-pless.conf <<'PLESS_EOF'
{SSHD_HARDENING}PLESS_EOF
chmod 644 /etc/ssh/sshd_config.d/60-pless.conf

cat > /etc/apt/apt.conf.d/52pless-auto-reboot <<'PLESS_EOF'
{UNATTENDED_AUTO_REBOOT}PLESS_EOF
chmod 644 /etc/apt/apt.conf.d/52pless-auto-reboot

usermod -aG docker {admin_user}
ufw allow OpenSSH
ufw --force enable
systemctl enable --now fail2ban
systemctl enable --now unattended-upgrades
systemctl restart ssh
"""
