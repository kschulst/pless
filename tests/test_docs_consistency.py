"""Guards against documentation drift.

A convention that says "remember to update the docs" decays. A failing build
does not. These tests fail when the documentation and the code disagree about
what commands exist, what configuration keys exist, or where links point.

They are deliberately mechanical: they check that documented things exist and
that existing things are documented. They cannot check whether the prose is
still *true* — that remains a human job.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from typer.main import get_command

from pless import config
from pless.cli import app

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
COMMANDS_PAGE = DOCS / "reference" / "commands.md"
CONFIG_PAGE = DOCS / "reference" / "configuration.md"

# Commands that exist but are intentionally not in the reference, because they
# are aliases or internal. Keep this list short and justified.
UNDOCUMENTED_BY_DESIGN: set[str] = {"version"}


def cli_commands() -> set[str]:
    """Every invocable command as the user types it, e.g. "storage init"."""
    found: set[str] = set()

    def walk(command, prefix: str = "") -> None:
        subcommands = getattr(command, "commands", None)
        if not subcommands:
            if prefix:
                found.add(prefix)
            return
        for name, sub in subcommands.items():
            walk(sub, f"{prefix} {name}".strip())

    walk(get_command(app))
    return found


def documented_commands() -> set[str]:
    """Commands documented as `### \\`pless <name> ...\\`` headings."""
    text = COMMANDS_PAGE.read_text()
    documented = set()
    for match in re.finditer(r"^### `pless ([^`]+)`", text, re.MULTILINE):
        # Strip arguments and flags: "docs scan <path> [--hashes]" -> "docs scan".
        words = []
        for word in match.group(1).split():
            if word.startswith(("<", "[", "-")):
                break
            words.append(word)
        if words:
            documented.add(" ".join(words))
    return documented


def config_sections() -> set[str]:
    """Top-level sections in the Config model, e.g. "target", "pi"."""
    return set(config.Config.model_fields)


class TestCommandDocs:
    def test_every_command_is_documented(self) -> None:
        missing = cli_commands() - documented_commands() - UNDOCUMENTED_BY_DESIGN
        assert not missing, (
            f"Commands exist but are not documented in {COMMANDS_PAGE.name}: "
            f"{sorted(missing)}. Add a `### \\`pless <name>\\`` section."
        )

    def test_no_documented_command_has_been_removed(self) -> None:
        stale = documented_commands() - cli_commands()
        assert not stale, (
            f"{COMMANDS_PAGE.name} documents commands that no longer exist: "
            f"{sorted(stale)}. Remove or rename those sections."
        )


class TestConfigDocs:
    def test_every_config_section_is_documented(self) -> None:
        text = CONFIG_PAGE.read_text()
        missing = [name for name in config_sections() if f"[{name}]" not in text]
        assert not missing, (
            f"Config sections exist but are not documented in {CONFIG_PAGE.name}: {missing}."
        )

    def test_pless_toml_only_uses_known_sections(self) -> None:
        with (REPO_ROOT / "pless.toml").open("rb") as f:
            data = tomllib.load(f)
        unknown = set(data) - config_sections()
        assert not unknown, f"pless.toml has sections the Config model does not know: {unknown}."

    def test_shipped_config_is_valid(self) -> None:
        # The example config must actually load, or the docs teach a broken file.
        config.load_config(REPO_ROOT / "pless.toml")


class TestLinks:
    """Relative links between docs pages must resolve."""

    @staticmethod
    def markdown_pages() -> list[Path]:
        return sorted(DOCS.rglob("*.md"))

    @pytest.mark.parametrize("page", markdown_pages.__func__())
    def test_relative_links_resolve(self, page: Path) -> None:
        broken = []
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", page.read_text()):
            href = match.group(1).split("#")[0].strip()
            if not href or href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (page.parent / href).resolve().exists():
                broken.append(href)
        assert not broken, f"{page.relative_to(REPO_ROOT)} links to missing files: {broken}"


class TestNavigation:
    def test_every_page_is_in_the_nav(self) -> None:
        with (REPO_ROOT / "zensical.toml").open("rb") as f:
            nav_text = str(tomllib.load(f)["project"]["nav"])
        missing = [
            str(page.relative_to(DOCS))
            for page in DOCS.rglob("*.md")
            if str(page.relative_to(DOCS)) not in nav_text
        ]
        assert not missing, (
            f"Pages exist but are missing from the nav in zensical.toml: {missing}. "
            "An unreachable page is a page nobody reads."
        )

    def test_every_nav_entry_exists(self) -> None:
        with (REPO_ROOT / "zensical.toml").open("rb") as f:
            nav_text = str(tomllib.load(f)["project"]["nav"])
        referenced = re.findall(r"'([^']+\.md)'", nav_text)
        missing = [name for name in referenced if not (DOCS / name).exists()]
        assert not missing, f"zensical.toml navigates to pages that do not exist: {missing}"
