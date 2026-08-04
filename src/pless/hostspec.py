"""Felles host-spec: hva enhver pless-server skal inneholde, som cloud-init user-data.

Samme spec brukes av alle targets som kan cloud-init (Multipass-VM, Ubuntu Server
på Pi via boot-partisjonen, Hetzner). Targets uten cloud-init får senere en
SSH-bootstrap som applikerer samme spec (beslutning #18).
"""

from __future__ import annotations

from pathlib import Path

import yaml

# apt venter selv på låsen i stedet for å feile med «Could not get lock».
APT_LOCK_TIMEOUT_SECONDS = 300

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

# LLMNR (port 5355) lytter på alle interfacer og er en kjent angrepsvektor
# (navneforgiftning på LAN). Vi har ingen bruk for det. mDNS (5353) røres
# bevisst IKKE — «vertsnavn.local» er dokumentert i førstegangsoppsettet.
RESOLVED_HARDENING = """\
[Resolve]
LLMNR=no
"""

PACKAGES = [
    "docker.io",
    "ufw",
    "fail2ban",
    "unattended-upgrades",
    "cryptsetup-bin",
    "curl",
]

# Docker-pakkene divergerer mellom distroene — begge verifisert empirisk, og
# ingen av dem trenger Dockers eget apt-repo:
#
#   Debian 13:   Compose v2 heter «docker-compose» (v2.26.1; «docker-compose-v2»
#                finnes ikke). Klienten er skilt ut i «docker-cli», som bare er
#                en Recommends av docker.io — og vi installerer med
#                --no-install-recommends, så den må listes eksplisitt.
#   Ubuntu 24.04+: Compose v2 heter «docker-compose-v2» («docker-compose» er
#                den utdaterte Python-v1-en). Klienten følger med docker.io.
DISTRO_PACKAGES = {
    "debian": ["docker-cli", "docker-compose"],
    "ubuntu": ["docker-compose-v2"],
}


def packages_for(distro_id: str = "debian") -> list[str]:
    extras = DISTRO_PACKAGES.get(distro_id)
    if extras is None:
        raise ValueError(
            f"Vet ikke hvilke docker-pakker {distro_id!r} bruker "
            f"(kjenner: {', '.join(DISTRO_PACKAGES)})."
        )
    return [*PACKAGES, *extras]


def read_pubkey(private_key_path: Path) -> str:
    pub_path = Path(str(private_key_path) + ".pub")
    if not pub_path.is_file():
        raise FileNotFoundError(f"Fant ikke offentlig nøkkel: {pub_path}")
    return pub_path.read_text().strip()


def build_user_data(
    ssh_pubkey: str, timezone: str, admin_user: str = "ubuntu", distro_id: str = "debian"
) -> dict:
    return {
        "timezone": timezone,
        "package_update": True,
        "package_upgrade": True,
        "packages": packages_for(distro_id),
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


def render_bootstrap_script(
    timezone: str, admin_user: str = "ubuntu", distro_id: str = "debian"
) -> str:
    """Samme spec som cloud-init, men som idempotent shell-script over SSH.

    Brukes når targetet allerede er booted og vi aldri rørte boot-partisjonen —
    typisk Pi etter Network Install (beslutning #18: felles spec, adapter per target).
    authorized_keys røres bevisst ikke: kommer vi inn over SSH, virker nøkkelen alt.
    """
    packages = " ".join(packages_for(distro_id))
    return f"""#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive

# En fersk maskin kjører gjerne cloud-init eller unattended-upgrades ved
# første boot. Uten dette kolliderer vi med apt-låsen og feiler.
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init status --wait >/dev/null 2>&1 || true
fi
APT="apt-get -o DPkg::Lock::Timeout={APT_LOCK_TIMEOUT_SECONDS}"

timedatectl set-timezone {timezone}

$APT update -qq
$APT install -y -qq --no-install-recommends {packages}

cat > /etc/ssh/sshd_config.d/60-pless.conf <<'PLESS_EOF'
{SSHD_HARDENING}PLESS_EOF
chmod 644 /etc/ssh/sshd_config.d/60-pless.conf

cat > /etc/apt/apt.conf.d/52pless-auto-reboot <<'PLESS_EOF'
{UNATTENDED_AUTO_REBOOT}PLESS_EOF
chmod 644 /etc/apt/apt.conf.d/52pless-auto-reboot

mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/60-pless.conf <<'PLESS_EOF'
{RESOLVED_HARDENING}PLESS_EOF
chmod 644 /etc/systemd/resolved.conf.d/60-pless.conf
systemctl restart systemd-resolved 2>/dev/null || true

usermod -aG docker {admin_user}
ufw allow OpenSSH
ufw --force enable
systemctl enable --now fail2ban
systemctl enable --now unattended-upgrades
systemctl restart ssh
"""
