"""Writing the `[host]` section of pless.toml.

The command that created a machine knows best how to reach it, and asking an
operator to copy a path by hand is an avoidable source of error — one whose
symptom is a cryptic SSH timeout much later. So provisioning writes the section
itself, says that it did, and refuses to quietly repoint a `[host]` that
already points somewhere else.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class HostFileError(RuntimeError):
    pass


@dataclass
class HostUpdate:
    address: str = ""
    user: str = ""
    key_path: str = ""
    port: int = 22
    ssh_config: str = ""
    ssh_alias: str = ""

    def render(self) -> str:
        lines = ["[host]"]
        if self.ssh_config:
            lines.append("# Written by pless. The referenced file tracks the machine's")
            lines.append("# current address, so a restart cannot leave this stale.")
            lines.append(f'ssh_config = "{self.ssh_config}"')
            lines.append(f'ssh_alias = "{self.ssh_alias}"')
        else:
            lines.append(f'address = "{self.address}"')
            lines.append(f'user = "{self.user}"')
            if self.key_path:
                lines.append(f'key_path = "{self.key_path}"')
            if self.port != 22:
                lines.append(f"port = {self.port}")
        return "\n".join(lines) + "\n"


def describes_same_machine(existing: dict, update: HostUpdate) -> bool:
    """Is the configured host already the one we are about to write?"""
    if update.ssh_config:
        return existing.get("ssh_alias", "") == update.ssh_alias
    return existing.get("address", "") == update.address


def replace_section(text: str, section: str, replacement: str) -> str:
    """Swap one TOML section, leaving every other line untouched.

    Done line by line rather than with a regex because comments sit *between*
    sections in the file, and a naive "everything until the next [" match
    swallows the explanatory comments belonging to the section that follows.
    Losing someone's notes from their own config file is not an acceptable
    side effect of pointing at a VM.
    """
    lines = text.splitlines(keepends=True)
    header = f"[{section}]"

    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        return text.rstrip() + "\n\n" + replacement

    # The section ends at the next header — but any comment block and blank
    # lines immediately above that header introduce it, so they stay.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            while end > start + 1 and lines[end - 1].strip().startswith(("#", "")):
                if lines[end - 1].strip() and not lines[end - 1].lstrip().startswith("#"):
                    break
                end -= 1
            break

    return "".join(lines[:start]) + replacement + "\n" + "".join(lines[end:])


def apply(config_path: Path, update: HostUpdate, force: bool = False) -> None:
    """Replace the [host] section, preserving everything else in the file."""
    if not config_path.is_file():
        raise HostFileError(f"{config_path} does not exist — run `pless init` first.")

    text = config_path.read_text()
    existing = tomllib.loads(text).get("host", {})
    configured = existing.get("address") or existing.get("ssh_alias")

    if configured and not describes_same_machine(existing, update) and not force:
        raise HostFileError(
            f"[host] already points at {configured!r}. Refusing to repoint it at "
            f"{update.ssh_alias or update.address!r} — if that is what you want, "
            "pass --force, but check first that you are not about to abandon a "
            "machine holding documents."
        )

    config_path.write_text(replace_section(text, "host", update.render()))
