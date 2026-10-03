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

    def test_shipped_config_names_nobody_in_particular(self) -> None:
        """`pless vm create` rewrites [host], and `git add -A` will commit that.

        It happened: a throwaway drill VM's absolute path, under one developer's
        home directory, reached main and pointed the checked-in config at a
        machine that no longer existed. The convention says examples use
        placeholders rather than anyone's real values, and a convention that
        relies on remembering is one that decays.
        """
        text = (REPO_ROOT / "pless.toml").read_text()
        assert "/Users/" not in text and "/home/" not in text, (
            "pless.toml contains an absolute path into someone's home directory. "
            "Use ~ or a placeholder."
        )
        assert "drill" not in text, (
            "pless.toml points at a throwaway VM. Restore it to the checked-in default."
        )


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


class TestTheDocumentedBootstrapCall:
    """The commands page carries the call `pless b2 provision` prints when it
    refuses a credential. Transcribing it by hand is how the two drift, and a
    drifted copy of this one is a setup that silently does not protect
    anything."""

    def test_the_page_carries_exactly_what_the_code_emits(self) -> None:
        from pless import b2

        page = COMMANDS_PAGE.read_text()
        assert b2.bootstrap_key_command("YOUR_ACCOUNT_ID") in page, (
            "docs/reference/commands.md no longer matches b2.bootstrap_key_command(). "
            "Regenerate it rather than editing it by hand."
        )


class TestArchitectureDecisionRecords:
    """ADRs live beside the code, not on the site, and the register must be complete."""

    ADR_DIR = REPO_ROOT / "adr"

    def records(self) -> list[Path]:
        return sorted(
            p for p in self.ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md") if p.stem != "0000-template"
        )

    def test_every_record_is_in_the_register(self) -> None:
        register = (self.ADR_DIR / "README.md").read_text()
        missing = [p.name for p in self.records() if p.name not in register]
        assert not missing, (
            f"ADRs exist but are not listed in adr/README.md: {missing}. "
            "An unregistered record is one nobody finds."
        )

    def test_numbers_are_unique_and_contiguous(self) -> None:
        numbers = [int(p.name[:4]) for p in self.records()]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"ADR numbers must be unique and contiguous from 0001; got {numbers}."
        )

    def test_each_record_states_a_status_and_date(self) -> None:
        incomplete = [
            p.name
            for p in self.records()
            if "- **Status:**" not in p.read_text() or "- **Date:**" not in p.read_text()
        ]
        assert not incomplete, f"ADRs missing a Status or Date line: {incomplete}"

    def test_records_are_not_published_to_the_site(self) -> None:
        # Publishing them without a nav entry would make them orphan pages:
        # reachable by URL but unlinked, which is neither private nor useful.
        assert not (DOCS / "adr").exists(), (
            "ADRs must stay outside docs/, or Zensical will build them as orphan pages."
        )


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


class TestVersionIsSingleSourced:
    """The git tag is the version. Nothing else may claim to know it.

    A hardcoded version that drifts from the tag publishes a package that
    misreports itself — and a released version cannot be corrected, only
    yanked.
    """

    def test_pyproject_declares_the_version_dynamic(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            project = tomllib.load(f)["project"]
        assert "version" not in project, (
            "pyproject.toml pins a static version. The version comes from the git "
            'tag via hatch-vcs; remove it and keep `dynamic = ["version"]`.'
        )
        assert "version" in project.get("dynamic", [])

    def test_no_module_hardcodes_a_version_string(self) -> None:
        offenders = [
            path.name
            for path in (REPO_ROOT / "src" / "pless").glob("*.py")
            if path.name != "_version.py"
            and re.search(r'^__version__\s*=\s*["\']\d', path.read_text(), re.MULTILINE)
        ]
        assert not offenders, (
            f"These modules hardcode a version: {offenders}. Read it from package "
            "metadata instead, so the tag stays the single source."
        )
