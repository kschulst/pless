"""Eksponeringsrevisjon: hva kan en angriper på LAN-et faktisk nå?

Trusselmodell: noen har allerede tilgang til det lokale nettet (kompromittert
wifi, gjesteenhet, IoT-dings). De skal ikke finne noen tjeneste å angripe, og
ikke kunne hente ut dokumenter.

Innsamling skjer i én SSH-runde; analysen er rene funksjoner slik at de kan
testes uten maskin — og gjenbrukes av et web-lag senere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pless import composegen, storage

# Loopback og Tailscale er greie å lytte på; alt annet er LAN-eksponering.
_LOOPBACK_PREFIXES = ("127.", "[::1]", "::1")


class Severity(StrEnum):
    CRITICAL = "kritisk"
    WARNING = "advarsel"


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


# Én runde over SSH. Seksjonsmarkører gjør utdataen parsebar uten å anta
# rekkefølge eller at hver kommando finnes.
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
    """Hent 'adresse:port' fra `ss -tlnH`-linjer (kolonne 4)."""
    addresses = []
    for line in ss_lines:
        fields = line.split()
        if len(fields) >= 4:
            addresses.append(fields[3])
    return addresses


# Gjør funnene lesbare: en portnummer alene sier lite om hva som må fikses.
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
            check="lyttende sockets",
            ok=True,
            detail="Ingenting lytter utenfor loopback — LAN-et ser null porter.",
        )
    described = ", ".join(describe_address(addr) for addr in exposed)
    return Finding(
        check="lyttende sockets",
        ok=False,
        detail=(
            f"Eksponert mot LAN: {described}. Kjør `pless harden` for å binde SSH til tailscale0."
        ),
    )


def check_ufw(ufw_lines: list[str]) -> Finding:
    text = " ".join(ufw_lines).lower()
    if "status: active" not in text:
        return Finding(check="brannmur", ok=False, detail="UFW er ikke aktiv.")
    if "deny (incoming)" not in text:
        return Finding(
            check="brannmur", ok=False, detail="UFW har ikke default deny på innkommende."
        )
    # «Anywhere» uten interface-binding betyr åpent mot LAN.
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
            check="brannmur",
            ok=False,
            detail=f"Regler åpne mot hele LAN-et: {'; '.join(open_to_lan)}",
        )
    return Finding(
        check="brannmur", ok=True, detail="UFW aktiv, default deny, ingen LAN-åpne regler."
    )


def check_docker_ports(docker_lines: list[str]) -> Finding:
    """Docker skriver egne iptables-regler FORBI UFW — en publisert port uten
    127.0.0.1-prefiks blir LAN-synlig selv om UFW sier deny."""
    leaked = []
    for line in docker_lines:
        name, _, ports = line.partition(" ")
        for mapping in ports.split(","):
            mapping = mapping.strip()
            if "->" not in mapping:
                continue  # kun container-intern port, ikke publisert
            if not mapping.startswith("127.0.0.1:"):
                leaked.append(f"{name}: {mapping}")
    if leaked:
        return Finding(
            check="docker-porter",
            ok=False,
            detail=(
                f"Publisert utenfor loopback (omgår UFW!): {'; '.join(leaked)}. "
                "Alle porter må bindes med 127.0.0.1-prefiks."
            ),
        )
    return Finding(
        check="docker-porter", ok=True, detail="Ingen container publiserer utenfor loopback."
    )


def check_sshd(sshd_lines: list[str]) -> Finding:
    settings = dict(line.split(None, 1) for line in sshd_lines if " " in line)
    if settings.get("passwordauthentication", "").strip() != "no":
        return Finding(check="ssh", ok=False, detail="Passord-innlogging er ikke avslått.")
    return Finding(check="ssh", ok=True, detail="Kun nøkkelbasert innlogging.")


def check_encrypted_storage(mount_lines: list[str]) -> Finding:
    source = mount_lines[0] if mount_lines else ""
    if not source:
        return Finding(
            check="kryptert lagring",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} er ikke montert — er disken låst?",
            severity=Severity.WARNING,
        )
    if storage.MAPPER_NAME not in source:
        return Finding(
            check="kryptert lagring",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} ligger på {source}, ikke på LUKS-enheten.",
        )
    return Finding(
        check="kryptert lagring", ok=True, detail=f"Data ligger på LUKS-enheten {source}."
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
