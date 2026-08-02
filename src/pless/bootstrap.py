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


@dataclass
class HostFacts:
    hostname: str
    model: str
    architecture: str
    os_pretty_name: str
    memory_gb: float

    @property
    def is_arm64(self) -> bool:
        return self.architecture == "aarch64"


def _run(target: TargetHost, command: str, timeout: int = 60) -> sshexec.SshResult:
    return sshexec.run(target.user, target.host, target.key, command, timeout=timeout)


def gather_facts(target: TargetHost) -> HostFacts:
    """Les maskinvare-/OS-fakta før vi endrer noe."""
    result = _run(
        target,
        "hostname; "
        # Modell-linja må alltid være ikke-tom: en pipe til `tr` returnerer 0 selv
        # når `cat` feiler, så `|| echo unknown` ville aldri slått inn.
        "M=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\\0'); echo \"${M:-ukjent}\"; "
        "uname -m; "
        '. /etc/os-release && echo "$PRETTY_NAME"; '
        "awk '/MemTotal/ {print $2}' /proc/meminfo",
    )
    if not result.ok:
        raise BootstrapError(f"Fikk ikke lest fakta fra targetet: {result.stderr.strip()}")
    return parse_facts(result.stdout)


def parse_facts(output: str) -> HostFacts:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    if len(lines) < 5:
        raise BootstrapError(f"Uventet fakta-output: {output!r}")
    hostname, model, arch, os_name, mem_kb = lines[0], lines[1], lines[2], lines[3], lines[4]
    return HostFacts(
        hostname=hostname,
        model=model,
        architecture=arch,
        os_pretty_name=os_name,
        memory_gb=round(int(mem_kb) / (1024 * 1024), 1),
    )


def apply(cfg: config.Config, target: TargetHost) -> None:
    """Kjør host-spec-scriptet. Idempotent — trygt å kjøre om igjen."""
    facts = gather_facts(target)
    if not facts.is_arm64 and cfg.target.type == "pi":
        raise BootstrapError(
            f"Targetet rapporterer arkitektur {facts.architecture!r}, ikke aarch64. "
            "Paperless-ngx 2.x finnes kun for arm64 — flash et 64-bit image."
        )

    script = hostspec.render_bootstrap_script(cfg.paperless.timezone, target.user)
    # Scriptet går på stdin til `sh -s`; ingenting havner i argv eller temp-filer.
    result = sshexec.run(
        target.user, target.host, target.key, "sudo sh -s", timeout=900, input_text=script
    )
    if not result.ok:
        raise BootstrapError(f"Bootstrap feilet: {result.stderr.strip() or result.stdout.strip()}")
