"""The host spec: what every pless machine must look like.

The same spec is delivered two ways, depending on what the target supports:
as cloud-init user-data, or as an idempotent shell script over SSH. Keeping
one spec with two renderers is the point — see the decision log.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Let apt wait for the lock instead of failing with "Could not get lock".
APT_LOCK_TIMEOUT_SECONDS = 300

SSHD_HARDENING = """\
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
"""

# Security updates are installed automatically, and a kernel update reboots
# the machine. The archive stays locked until `pless unlock` runs.
UNATTENDED_AUTO_REBOOT = """\
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
"""

# LLMNR (port 5355) listens on every interface and is a known poisoning
# vector on a local network. We have no use for it. mDNS (5353) is left
# alone on purpose: "hostname.local" is documented in the install guide.
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
    # Backup runs on the target, so restic lives there. Both Debian 13 and
    # Ubuntu 24.04 carry it in the main archive; no third-party repository.
    "restic",
]

# The Docker packages differ between the distributions. Both verified
# empirically, and neither needs Docker's own apt repository:
#
#   Debian 13:     Compose v2 is called "docker-compose" (v2.26.1); there is
#                  no "docker-compose-v2". The client is split out into
#                  "docker-cli", which is only a Recommends of docker.io —
#                  and we install with --no-install-recommends, so it has to
#                  be listed explicitly or you get a daemon with no client.
#   Ubuntu 24.04+: Compose v2 is "docker-compose-v2" ("docker-compose" is the
#                  obsolete Python v1). The client ships with docker.io.
DISTRO_PACKAGES = {
    "debian": ["docker-cli", "docker-compose"],
    "ubuntu": ["docker-compose-v2"],
}


def packages_for(distro_id: str = "debian") -> list[str]:
    extras = DISTRO_PACKAGES.get(distro_id)
    if extras is None:
        raise ValueError(
            f"Unknown Docker packages for distribution {distro_id!r} "
            f"(known: {', '.join(DISTRO_PACKAGES)})."
        )
    return [*PACKAGES, *extras]


def read_pubkey(private_key_path: Path) -> str:
    pub_path = Path(str(private_key_path) + ".pub")
    if not pub_path.is_file():
        raise FileNotFoundError(f"Public key not found: {pub_path}")
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
            {
                "path": "/etc/systemd/resolved.conf.d/60-pless.conf",
                "content": RESOLVED_HARDENING,
                "permissions": "0644",
            },
        ],
        "runcmd": [
            f"usermod -aG docker {admin_user}",
            "ufw allow OpenSSH",
            "ufw --force enable",
            "systemctl enable --now fail2ban",
            "systemctl enable --now unattended-upgrades",
            "systemctl restart systemd-resolved",
            "systemctl restart ssh",
        ],
    }


def render_user_data(ssh_pubkey: str, timezone: str, admin_user: str = "ubuntu") -> str:
    spec = build_user_data(ssh_pubkey, timezone, admin_user)
    return "#cloud-config\n" + yaml.safe_dump(spec, sort_keys=False, width=120)


def render_bootstrap_script(
    timezone: str, admin_user: str = "ubuntu", distro_id: str = "debian"
) -> str:
    """The same spec as cloud-init, as an idempotent shell script over SSH.

    Used when the target booted before we ever saw its boot partition —
    typically a Pi installed with Raspberry Pi Imager.

    authorized_keys is deliberately untouched: if we got in over SSH, the key
    already works, and the script must not be able to lock us out.
    """
    packages = " ".join(packages_for(distro_id))
    return f"""#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive

# A freshly installed machine tends to run cloud-init or unattended-upgrades
# on first boot. Without this we collide with the apt lock and fail.
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
