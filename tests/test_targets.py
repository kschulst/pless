import json

import pytest

from pless.targets import TargetError, parse_multipass_ip


def info_json(state: str = "Running", ipv4: list[str] | None = None) -> str:
    return json.dumps(
        {"info": {"pless-dev": {"state": state, "ipv4": ipv4 if ipv4 is not None else []}}}
    )


def test_parses_ip_from_running_vm() -> None:
    assert parse_multipass_ip(info_json(ipv4=["192.168.64.5"]), "pless-dev") == "192.168.64.5"


def test_unknown_vm_raises() -> None:
    with pytest.raises(TargetError, match="kjenner ikke"):
        parse_multipass_ip(info_json(), "annen-vm")


def test_stopped_vm_raises() -> None:
    with pytest.raises(TargetError, match="ikke Running"):
        parse_multipass_ip(info_json(state="Stopped", ipv4=["10.0.0.1"]), "pless-dev")


def test_running_without_ip_raises() -> None:
    with pytest.raises(TargetError, match="ingen IPv4"):
        parse_multipass_ip(info_json(ipv4=[]), "pless-dev")
