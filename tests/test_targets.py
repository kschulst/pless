"""There is one kind of target: a machine reachable over SSH.

These tests pin down that the connection can be expressed either directly or
through an SSH config file — the latter being what makes a VM with a changing
port an ordinary host rather than a special case.
"""

from pathlib import Path

import pytest

from pless.config import Config, HostConfig
from pless.targets import TargetError, build_ssh_args, resolve_host


class TestDirectConnection:
    def test_builds_destination_from_address_and_user(self) -> None:
        args = build_ssh_args(HostConfig(address="archive.local", user="admin"))
        assert args[-1] == "admin@archive.local"
        assert "-p" in args and "22" in args

    def test_non_default_port_is_carried(self) -> None:
        args = build_ssh_args(HostConfig(address="10.0.0.5", user="root", port=2222))
        assert args[args.index("-p") + 1] == "2222"

    def test_unconfigured_host_explains_both_ways_out(self) -> None:
        with pytest.raises(TargetError) as exc:
            build_ssh_args(HostConfig())
        assert "address" in str(exc.value) and "pless vm create" in str(exc.value)


class TestSshConfigConnection:
    def test_uses_dash_capital_f_and_the_alias(self, tmp_path: Path) -> None:
        conf = tmp_path / "ssh.config"
        conf.write_text("Host box\n  Hostname 127.0.0.1\n  Port 60022\n")
        args = build_ssh_args(HostConfig(ssh_config=str(conf), ssh_alias="box"))
        assert args == ["-F", str(conf), "box"]

    def test_missing_config_file_is_explained(self, tmp_path: Path) -> None:
        host = HostConfig(ssh_config=str(tmp_path / "gone.config"), ssh_alias="box")
        with pytest.raises(TargetError, match="does not exist"):
            build_ssh_args(host)

    def test_both_fields_are_required_to_use_this_mode(self) -> None:
        # An alias without a file, or a file without an alias, falls back to
        # the direct form rather than silently producing a broken command.
        assert not HostConfig(ssh_config="/tmp/x", ssh_alias="").uses_ssh_config
        assert not HostConfig(ssh_config="", ssh_alias="box").uses_ssh_config


class TestLabel:
    def test_alias_is_the_label_when_using_a_config(self) -> None:
        assert HostConfig(ssh_config="/tmp/x", ssh_alias="lima-dev").label == "lima-dev"

    def test_address_is_the_label_otherwise(self) -> None:
        assert HostConfig(address="archive.local").label == "archive.local"

    def test_unconfigured_says_so(self) -> None:
        assert "unconfigured" in HostConfig().label


def test_resolve_host_carries_the_label(tmp_path: Path) -> None:
    conf = tmp_path / "ssh.config"
    conf.write_text("Host box\n")
    cfg = Config(host=HostConfig(ssh_config=str(conf), ssh_alias="box"))
    host = resolve_host(cfg)
    assert host.label == "box"
    assert host.ssh_args == ["-F", str(conf), "box"]


def test_tunnel_args_forward_a_port_without_a_remote_command() -> None:
    cfg = Config(host=HostConfig(address="archive.local", user="admin"))
    args = resolve_host(cfg).tunnel_args(8000, 8000)
    assert "-L" in args and "8000:127.0.0.1:8000" in args
    assert "-N" in args
