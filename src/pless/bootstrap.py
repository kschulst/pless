"""Applisér host-spec over SSH på et target som allerede er booted.

Mekanisme-tvilling til cloud-init (beslutning #18). Pi-en flashes med Network
Install og booter FØR vi noensinne ser boot-partisjonen — da leverer vi samme
spec over SSH i stedet.
"""

from __future__ import annotations

from dataclasses import dataclass

from pless import config, hostspec, sshexec
from pless.targets import TargetHost


class BootstrapError(RuntimeError):
    pass


# Distroer bootstrap er validert mot, likestilt: Debian via Lima og RPi OS Trixie,
# Ubuntu via Multipass og Hetzner. Debian 13 er minimum fordi docker-compose-v2
# først finnes i apt der; Debian 12 ville krevd Dockers eget repo.
# Raspberry Pi OS 64-bit rapporterer ID=debian, så den dekkes av "debian".
SUPPORTED_DISTROS = {
    "debian": (13, 0),
    "ubuntu": (24, 4),
}

# Paperless-ngx 2.x bygges ikke for 32-bit ARM.
SUPPORTED_ARCHITECTURES = {"aarch64", "x86_64"}


@dataclass
class HostFacts:
    hostname: str
    model: str
    architecture: str
    distro_id: str
    distro_version: str
    os_pretty_name: str
    memory_gb: float

    @property
    def is_arm64(self) -> bool:
        return self.architecture == "aarch64"

    @property
    def is_raspberry_pi(self) -> bool:
        return "raspberry pi" in self.model.lower()

    def version_tuple(self) -> tuple[int, ...]:
        parts: list[int] = []
        for chunk in self.distro_version.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts) or (0,)

    @property
    def distro_supported(self) -> bool:
        minimum = SUPPORTED_DISTROS.get(self.distro_id)
        if minimum is None:
            return False
        current = self.version_tuple()
        # Sammenlikn bare like mange ledd som minimumskravet har.
        padded = tuple(current[i] if i < len(current) else 0 for i in range(len(minimum)))
        return padded >= minimum


def _run(target: TargetHost, command: str, timeout: int = 60) -> sshexec.SshResult:
    return sshexec.run(
        target.user, target.host, target.key, command, timeout=timeout, port=target.port
    )


# Nøkkel=verdi i stedet for posisjonelle linjer: robust mot at et felt mangler
# eller at en kommando skriver en ekstra linje.
_FACTS_SCRIPT = """\
echo "hostname=$(hostname)"
M=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\\0')
echo "model=${M:-ukjent}"
echo "arch=$(uname -m)"
. /etc/os-release 2>/dev/null || true
echo "distro_id=${ID:-ukjent}"
echo "distro_version=${VERSION_ID:-0}"
echo "os_name=${PRETTY_NAME:-ukjent}"
echo "mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
"""


def gather_facts(target: TargetHost) -> HostFacts:
    """Les maskinvare-/OS-fakta før vi endrer noe."""
    result = _run(target, _FACTS_SCRIPT)
    if not result.ok:
        raise BootstrapError(f"Fikk ikke lest fakta fra targetet: {result.stderr.strip()}")
    return parse_facts(result.stdout)


def parse_facts(output: str) -> HostFacts:
    fields: dict[str, str] = {}
    for line in output.strip().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()

    required = ("hostname", "arch", "distro_id", "mem_kb")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise BootstrapError(f"Mangler fakta {missing} i output: {output!r}")

    return HostFacts(
        hostname=fields["hostname"],
        model=fields.get("model", "ukjent"),
        architecture=fields["arch"],
        distro_id=fields["distro_id"].strip('"').lower(),
        distro_version=fields.get("distro_version", "0").strip('"'),
        os_pretty_name=fields.get("os_name", "ukjent").strip('"'),
        memory_gb=round(int(fields["mem_kb"]) / (1024 * 1024), 1),
    )


def apply(cfg: config.Config, target: TargetHost, facts: HostFacts | None = None) -> None:
    """Kjør host-spec-scriptet. Idempotent — trygt å kjøre om igjen."""
    facts = facts or gather_facts(target)
    if facts.architecture not in SUPPORTED_ARCHITECTURES:
        raise BootstrapError(
            f"Arkitekturen {facts.architecture!r} støttes ikke — Paperless-ngx 2.x "
            "finnes kun for arm64 og x86_64. På Raspberry Pi: flash et 64-bit image."
        )
    if not facts.distro_supported:
        supported = ", ".join(
            f"{name} {ver[0]}.{ver[1]}+" for name, ver in SUPPORTED_DISTROS.items()
        )
        raise BootstrapError(
            f"Distroen {facts.distro_id} {facts.distro_version} er ikke støttet. "
            f"Bootstrap er validert mot: {supported}."
        )

    script = hostspec.render_bootstrap_script(
        cfg.paperless.timezone, target.user, distro_id=facts.distro_id
    )
    # Scriptet går på stdin til `sh -s`; ingenting havner i argv eller temp-filer.
    result = sshexec.run(
        target.user,
        target.host,
        target.key,
        "sudo sh -s",
        timeout=900,
        input_text=script,
        port=target.port,
    )
    if not result.ok:
        raise BootstrapError(f"Bootstrap feilet: {result.stderr.strip() or result.stdout.strip()}")
