"""Creating a working directory from the templates bundled in the package.

Installing from PyPI gives you a `pless` binary and nothing else — no
pless.toml, no .env.example. Both ship inside the package so that `pless init`
works identically whether the tool was installed with pip or run from a clone.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

CONFIG_TEMPLATE = "pless.toml"
ENV_TEMPLATE = "env.example"  # not ".env.example": leading dots confuse packaging


@dataclass
class ScaffoldResult:
    created: list[str]
    kept: list[str]

    @property
    def anything_created(self) -> bool:
        return bool(self.created)


def read_template(name: str) -> str:
    return (resources.files("pless.templates") / name).read_text(encoding="utf-8")


def scaffold(directory: Path) -> ScaffoldResult:
    """Write pless.toml and .env.example if absent. Never overwrites."""
    created: list[str] = []
    kept: list[str] = []

    for target_name, template_name in (
        ("pless.toml", CONFIG_TEMPLATE),
        (".env.example", ENV_TEMPLATE),
    ):
        target = directory / target_name
        if target.exists():
            kept.append(target_name)
            continue
        target.write_text(read_template(template_name), encoding="utf-8")
        created.append(target_name)

    return ScaffoldResult(created=created, kept=kept)
