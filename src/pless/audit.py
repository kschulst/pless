"""Exposure audit: what can someone already on your network actually reach?

Threat model: an attacker already has access to the local network — a guest's
laptop, an unpatched IoT device, a compromised phone. They should find no
service to attack and no way to extract documents.

Collection happens in a single SSH round trip; the analysis is pure functions
so it can be tested without a machine, and reused by a web layer later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pless import composegen, storage

# Loopback is fine to listen on; anything else is LAN exposure.
_LOOPBACK_PREFIXES = ("127.", "[::1]", "::1")


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str
    severity: Severity = Severity.CRITICAL


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    @property
    def ok(self) -> bool:
        return not self.failures


# One round trip over SSH. Section markers make the output parseable without
# assuming an order, or that every command exists.
COLLECT_SCRIPT = f"""\
echo "##LISTEN"
sudo ss -tlnH 2>/dev/null || true
echo "##UFW"
sudo ufw status verbose 2>/dev/null || true
echo "##DOCKER"
sudo docker ps --format '{{{{.Names}}}} {{{{.Ports}}}}' 2>/dev/null || true
echo "##SSHD"
sudo sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin)' || true
echo "##MOUNT"
findmnt -no SOURCE {composegen.INSTALL_DIR} 2>/dev/null || true
"""


def split_sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("##"):
            current = line[2:].strip().lower()
            sections[current] = []
        elif current:
            stripped = line.strip()
            if stripped:
                sections[current].append(stripped)
    return sections


def _is_loopback(address: str) -> bool:
    return any(address.startswith(prefix) for prefix in _LOOPBACK_PREFIXES)


def parse_listening_addresses(ss_lines: list[str]) -> list[str]:
    """Extract 'address:port' from `ss -tlnH` lines (column 4)."""
    addresses = []
    for line in ss_lines:
        fields = line.split()
        if len(fields) >= 4:
            addresses.append(fields[3])
    return addresses


# Make findings readable: a bare port number says little about what to fix.
KNOWN_PORTS = {
    "22": "SSH",
    "5353": "mDNS",
    "5355": "LLMNR",
    "8000": "Paperless",
    "5432": "Postgres",
    "6379": "Redis",
}


def describe_address(address: str) -> str:
    port = address.rsplit(":", 1)[-1]
    name = KNOWN_PORTS.get(port)
    return f"{address} ({name})" if name else address


def check_listening_sockets(ss_lines: list[str]) -> Finding:
    exposed = [addr for addr in parse_listening_addresses(ss_lines) if not _is_loopback(addr)]
    if not exposed:
        return Finding(
            check="listening sockets",
            ok=True,
            detail="Nothing listens outside loopback — your LAN sees zero ports.",
        )
    described = ", ".join(describe_address(addr) for addr in exposed)
    return Finding(
        check="listening sockets",
        ok=False,
        detail=(f"Exposed to LAN: {described}. Run `pless harden` to bind SSH to tailscale0."),
    )


def check_ufw(ufw_lines: list[str]) -> Finding:
    text = " ".join(ufw_lines).lower()
    if "status: active" not in text:
        return Finding(check="firewall", ok=False, detail="UFW is not active.")
    if "deny (incoming)" not in text:
        return Finding(
            check="firewall", ok=False, detail="UFW does not default-deny incoming traffic."
        )
    # "Anywhere" without an interface binding means open to the LAN.
    open_to_lan = [
        line
        for line in ufw_lines
        if "allow in" in line.lower()
        and "anywhere" in line.lower()
        and "tailscale" not in line.lower()
        and "(v6)" not in line.lower()
    ]
    if open_to_lan:
        return Finding(
            check="firewall",
            ok=False,
            detail=f"Rules open to the whole LAN: {'; '.join(open_to_lan)}",
        )
    return Finding(check="firewall", ok=True, detail="UFW active, default deny, no LAN-open rules.")


def check_docker_ports(docker_lines: list[str]) -> Finding:
    """Docker writes its own iptables rules, bypassing UFW — a port published
    without a 127.0.0.1 prefix is LAN-visible even though UFW says deny."""
    leaked = []
    for line in docker_lines:
        name, _, ports = line.partition(" ")
        for mapping in ports.split(","):
            mapping = mapping.strip()
            if "->" not in mapping:
                continue  # container-internal port only, not published
            if not mapping.startswith("127.0.0.1:"):
                leaked.append(f"{name}: {mapping}")
    if leaked:
        return Finding(
            check="docker ports",
            ok=False,
            detail=(
                f"Published outside loopback (bypasses UFW!): {'; '.join(leaked)}. "
                "All ports must be bound with a 127.0.0.1 prefix."
            ),
        )
    return Finding(check="docker ports", ok=True, detail="No container publishes outside loopback.")


def check_sshd(sshd_lines: list[str]) -> Finding:
    settings = dict(line.split(None, 1) for line in sshd_lines if " " in line)
    if settings.get("passwordauthentication", "").strip() != "no":
        return Finding(check="ssh", ok=False, detail="Password authentication is not disabled.")
    return Finding(check="ssh", ok=True, detail="Key-based authentication only.")


def check_encrypted_storage(mount_lines: list[str]) -> Finding:
    source = mount_lines[0] if mount_lines else ""
    if not source:
        return Finding(
            check="encrypted storage",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} is not mounted — is the volume locked?",
            severity=Severity.WARNING,
        )
    if storage.MAPPER_NAME not in source:
        return Finding(
            check="encrypted storage",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} is on {source}, not on the LUKS device.",
        )
    return Finding(
        check="encrypted storage", ok=True, detail=f"Data lives on the LUKS device {source}."
    )


def analyse(output: str) -> AuditReport:
    sections = split_sections(output)
    return AuditReport(
        findings=[
            check_listening_sockets(sections.get("listen", [])),
            check_ufw(sections.get("ufw", [])),
            check_docker_ports(sections.get("docker", [])),
            check_sshd(sections.get("sshd", [])),
            check_encrypted_storage(sections.get("mount", [])),
        ]
    )
