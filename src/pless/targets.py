"""Resolving `[host]` into an SSH destination.

There is exactly one kind of target: a machine reachable over SSH. What created
it is not this module's concern — see `vm` and `hetzner` for optional ways to
obtain one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pless import config


class TargetError(RuntimeError):
    pass


@dataclass
class Host:
    """A resolved, connectable machine."""

    label: str
    ssh_args: list[str]

    def tunnel_args(self, local_port: int, remote_port: int) -> list[str]:
        return ["-L", f"{local_port}:127.0.0.1:{remote_port}", "-N", *self.ssh_args]


def build_ssh_args(host: config.HostConfig) -> list[str]:
    """Turn host configuration into the destination part of an ssh command."""
    if host.uses_ssh_config:
        path = Path(host.ssh_config).expanduser()
        if not path.is_file():
            raise TargetError(
                f"[host] ssh_config points at {path}, which does not exist. "
                "If the VM was deleted, recreate it or point [host] somewhere else."
            )
        return ["-F", str(path), host.ssh_alias]

    if not host.address:
        raise TargetError(
            "[host] is not configured in pless.toml. Set `address` and `user`, or run "
            "`pless vm create` to have a local machine made for you."
        )
    return ["-p", str(host.port), "-i", str(host.key), f"{host.user}@{host.address}"]


def resolve_host(cfg: config.Config) -> Host:
    return Host(label=cfg.host.label, ssh_args=build_ssh_args(cfg.host))
