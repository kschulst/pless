import json

import pytest

from pless.targets import TargetError, _run_tool, parse_lima_instance


def lima_line(name: str = "pless-dev", status: str = "Running", port: int = 60022) -> str:
    return json.dumps(
        {
            "name": name,
            "status": status,
            "sshLocalPort": port,
            "config": {"user": {"name": "deploy"}},
        }
    )


def test_parses_user_and_port() -> None:
    assert parse_lima_instance(lima_line(), "pless-dev") == ("deploy", 60022)


def test_finds_the_right_instance_among_several() -> None:
    output = "\n".join([lima_line("annen", port=60001), lima_line("pless-dev", port=60022)])
    assert parse_lima_instance(output, "pless-dev")[1] == 60022


def test_stopped_instance_raises() -> None:
    with pytest.raises(TargetError, match="is not Running"):
        parse_lima_instance(lima_line(status="Stopped"), "pless-dev")


def test_unknown_instance_raises() -> None:
    with pytest.raises(TargetError, match="does not know"):
        parse_lima_instance(lima_line(), "finnes-ikke")


def test_missing_port_raises() -> None:
    with pytest.raises(TargetError, match="no SSH port"):
        parse_lima_instance(json.dumps({"name": "pless-dev", "status": "Running"}), "pless-dev")


def test_non_json_lines_are_skipped() -> None:
    # limactl can print warnings to stdout before the JSON.
    assert parse_lima_instance(f"WARN noe skjedde\n{lima_line()}", "pless-dev")[1] == 60022


def test_missing_tool_gives_explanation_not_traceback() -> None:
    with pytest.raises(TargetError, match="was not found in PATH"):
        _run_tool(["dette-verktoyet-finnes-ikke", "--hjelp"])


def test_missing_known_tool_includes_install_hint() -> None:
    # We cannot call limactl here (it may be installed), so check the hint table.
    from pless.targets import _INSTALL_HINTS

    assert "brew install lima" in _INSTALL_HINTS["limactl"]
    assert "multipass" in _INSTALL_HINTS["multipass"]
