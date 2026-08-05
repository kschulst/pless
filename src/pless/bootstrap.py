"""Apply the host spec over SSH to a machine that has already booted.

The mechanism twin of cloud-init. A Pi installed with Raspberry Pi Imager
boots before we ever see its boot partition, so we deliver the same spec over
SSH instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from pless import config, hostspec, sshexec
from pless.targets import Host

# Distributions bootstrap is validated against, treated as equals: Debian via
# Lima and Raspberry Pi OS, Ubuntu via Multipass and Hetzner.
#
# Debian 13 is the minimum because docker-compose-v2 only reaches apt there;
# Debian 12 would have required Docker's own repository. Raspberry Pi OS
# 64-bit reports ID=debian, so it is covered by "debian".
SUPPORTED_DISTROS = {
    "debian": (13, 0),
    "ubuntu": (24, 4),
}

# Paperless-ngx 2.x is not built for 32-bit ARM.
SUPPORTED_ARCHITECTURES = {"aarch64", "x86_64"}


class BootstrapError(RuntimeError):
    pass


@dataclass
class HostFacts:
    hostname: str
    user: str
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
        # Compare only as many components as the minimum specifies.
        padded = tuple(current[i] if i < len(current) else 0 for i in range(len(minimum)))
        return padded >= minimum


def _run(target: Host, command: str, timeout: int = 60) -> sshexec.SshResult:
    return sshexec.run(target.ssh_args, command, timeout=timeout)


# Key=value rather than positional lines, so a missing or extra field does not
# shift everything after it.
_FACTS_SCRIPT = """\
echo "hostname=$(hostname)"
echo "user=$(whoami)"
M=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\\0')
echo "model=${M:-unknown}"
echo "arch=$(uname -m)"
. /etc/os-release 2>/dev/null || true
echo "distro_id=${ID:-unknown}"
echo "distro_version=${VERSION_ID:-0}"
echo "os_name=${PRETTY_NAME:-unknown}"
echo "mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
"""


def gather_facts(target: Host) -> HostFacts:
    """Read hardware and OS facts before changing anything."""
    result = _run(target, _FACTS_SCRIPT)
    if not result.ok:
        raise BootstrapError(f"Could not read facts from the target: {result.stderr.strip()}")
    return parse_facts(result.stdout)


def parse_facts(output: str) -> HostFacts:
    fields: dict[str, str] = {}
    for line in output.strip().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()

    required = ("hostname", "user", "arch", "distro_id", "mem_kb")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise BootstrapError(f"Missing facts {missing} in output: {output!r}")

    return HostFacts(
        hostname=fields["hostname"],
        user=fields["user"],
        model=fields.get("model", "unknown"),
        architecture=fields["arch"],
        distro_id=fields["distro_id"].strip('"').lower(),
        distro_version=fields.get("distro_version", "0").strip('"'),
        os_pretty_name=fields.get("os_name", "unknown").strip('"'),
        memory_gb=round(int(fields["mem_kb"]) / (1024 * 1024), 1),
    )


def apply(cfg: config.Config, target: Host, facts: HostFacts | None = None) -> None:
    """Run the host spec script. Idempotent — safe to run again."""
    facts = facts or gather_facts(target)
    if facts.architecture not in SUPPORTED_ARCHITECTURES:
        raise BootstrapError(
            f"Architecture {facts.architecture!r} is not supported — Paperless-ngx 2.x "
            "is built only for arm64 and x86_64. On a Raspberry Pi, flash a 64-bit image."
        )
    if not facts.distro_supported:
        supported = ", ".join(
            f"{name} {ver[0]}.{ver[1]}+" for name, ver in SUPPORTED_DISTROS.items()
        )
        raise BootstrapError(
            f"Distribution {facts.distro_id} {facts.distro_version} is not supported. "
            f"Bootstrap is validated against: {supported}."
        )

    # The account we logged in as is the one that needs docker access —
    # asked of the machine rather than assumed from configuration, so it holds
    # however the connection was expressed.
    script = hostspec.render_bootstrap_script(
        cfg.paperless.timezone, facts.user, distro_id=facts.distro_id
    )
    # The script goes to `sh -s` on stdin; nothing lands in argv or a temp file.
    result = sshexec.run(target.ssh_args, "sudo sh -s", timeout=900, input_text=script)
    if not result.ok:
        raise BootstrapError(f"Bootstrap failed: {result.stderr.strip() or result.stdout.strip()}")
