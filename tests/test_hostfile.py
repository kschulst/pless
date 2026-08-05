"""Writing [host] into pless.toml.

The command that created a machine knows how to reach it, so it writes the
section itself. That is only safe if it cannot silently repoint a config at a
different machine — abandoning one that may hold documents.
"""

import tomllib
from pathlib import Path

import pytest

from pless.hostfile import HostFileError, HostUpdate, apply, describes_same_machine

CONFIG = """\
[host]
address = "old.local"
user = "root"

[storage]
data_size_gb = 100
"""


def write(tmp_path: Path, text: str = CONFIG) -> Path:
    path = tmp_path / "pless.toml"
    path.write_text(text)
    return path


class TestRendering:
    def test_ssh_config_form(self) -> None:
        rendered = HostUpdate(ssh_config="/tmp/ssh.config", ssh_alias="box").render()
        assert 'ssh_config = "/tmp/ssh.config"' in rendered
        assert 'ssh_alias = "box"' in rendered
        # The two forms are exclusive: writing one must not leave the other behind.
        assert "address =" not in rendered

    def test_direct_form(self) -> None:
        rendered = HostUpdate(address="10.0.0.5", user="admin", port=2222).render()
        assert 'address = "10.0.0.5"' in rendered
        assert "port = 2222" in rendered

    def test_default_port_is_left_out(self) -> None:
        assert "port" not in HostUpdate(address="a", user="b").render()


class TestApply:
    def test_replaces_the_section_and_keeps_the_rest(self, tmp_path: Path) -> None:
        path = write(tmp_path)
        apply(path, HostUpdate(address="old.local", user="admin"))
        data = tomllib.loads(path.read_text())
        assert data["host"]["user"] == "admin"
        assert data["storage"]["data_size_gb"] == 100  # untouched

    def test_adds_the_section_when_absent(self, tmp_path: Path) -> None:
        path = write(tmp_path, "[storage]\ndata_size_gb = 50\n")
        apply(path, HostUpdate(address="new.local", user="root"))
        data = tomllib.loads(path.read_text())
        assert data["host"]["address"] == "new.local"
        assert data["storage"]["data_size_gb"] == 50

    def test_refuses_to_repoint_at_a_different_machine(self, tmp_path: Path) -> None:
        path = write(tmp_path)
        with pytest.raises(HostFileError, match="already points at"):
            apply(path, HostUpdate(address="somewhere-else.local", user="root"))
        assert "old.local" in path.read_text()  # unchanged

    def test_force_allows_repointing(self, tmp_path: Path) -> None:
        path = write(tmp_path)
        apply(path, HostUpdate(address="somewhere-else.local", user="root"), force=True)
        assert tomllib.loads(path.read_text())["host"]["address"] == "somewhere-else.local"

    def test_rewriting_the_same_machine_needs_no_force(self, tmp_path: Path) -> None:
        # Re-running `vm create` against an existing VM must not require --force.
        path = write(tmp_path)
        apply(path, HostUpdate(address="old.local", user="root"))
        assert tomllib.loads(path.read_text())["host"]["address"] == "old.local"

    def test_missing_file_says_to_run_init(self, tmp_path: Path) -> None:
        with pytest.raises(HostFileError, match="pless init"):
            apply(tmp_path / "nope.toml", HostUpdate(address="a", user="b"))


class TestSameMachine:
    def test_matches_on_alias_for_ssh_config_form(self) -> None:
        update = HostUpdate(ssh_config="/tmp/x", ssh_alias="box")
        assert describes_same_machine({"ssh_alias": "box"}, update)
        assert not describes_same_machine({"ssh_alias": "other"}, update)

    def test_matches_on_address_otherwise(self) -> None:
        update = HostUpdate(address="a.local", user="root")
        assert describes_same_machine({"address": "a.local"}, update)
        assert not describes_same_machine({"address": "b.local"}, update)


class TestPreservesTheRestOfTheFile:
    """Editing someone's config must not eat the notes they wrote in it."""

    ANNOTATED = """\
[host]
address = "old.local"
user = "root"

# --- Optional sections below -----------------------------------------------

# This comment explains storage, and belongs to it.
[storage]
data_size_gb = 100

# This one explains vm.
[vm]
backend = "lima"
"""

    def test_comments_introducing_later_sections_survive(self, tmp_path: Path) -> None:
        path = write(tmp_path, self.ANNOTATED)
        apply(path, HostUpdate(address="old.local", user="admin"))
        result = path.read_text()

        assert "# --- Optional sections below" in result
        assert "# This comment explains storage, and belongs to it." in result
        assert "# This one explains vm." in result

    def test_later_sections_keep_their_values(self, tmp_path: Path) -> None:
        path = write(tmp_path, self.ANNOTATED)
        apply(path, HostUpdate(address="old.local", user="admin"))
        data = tomllib.loads(path.read_text())
        assert data["storage"]["data_size_gb"] == 100
        assert data["vm"]["backend"] == "lima"
        assert data["host"]["user"] == "admin"

    def test_result_is_still_valid_toml(self, tmp_path: Path) -> None:
        path = write(tmp_path, self.ANNOTATED)
        apply(path, HostUpdate(ssh_config="/tmp/x", ssh_alias="old.local"), force=True)
        tomllib.loads(path.read_text())  # raises if we produced garbage

    def test_a_trailing_host_section_is_replaced_cleanly(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[storage]\ndata_size_gb = 50\n\n[host]\naddress = "a"\n')
        apply(path, HostUpdate(address="a", user="admin"))
        data = tomllib.loads(path.read_text())
        assert data["host"]["user"] == "admin"
        assert data["storage"]["data_size_gb"] == 50
