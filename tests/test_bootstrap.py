import pytest

from pless.bootstrap import BootstrapError, parse_facts
from pless.hostspec import render_bootstrap_script

PI5_FACTS = """\
testvert
Raspberry Pi 5 Model B Rev 1.0
aarch64
Ubuntu 24.04.4 LTS
8244480
"""


def test_parse_facts_reads_hostname_model_arch_and_memory() -> None:
    facts = parse_facts(PI5_FACTS)
    assert facts.hostname == "testvert"
    assert facts.model == "Raspberry Pi 5 Model B Rev 1.0"
    assert facts.architecture == "aarch64"
    assert facts.os_pretty_name == "Ubuntu 24.04.4 LTS"
    assert facts.memory_gb == 7.9
    assert facts.is_arm64


def test_any_hostname_is_accepted() -> None:
    # Verktøyet skal ikke ha meninger om hva boksen heter.
    for name in ("paperless", "testvert", "arkiv-01", "pi"):
        facts = parse_facts(f"{name}\nRaspberry Pi 5\naarch64\nUbuntu 24.04 LTS\n8244480\n")
        assert facts.hostname == name


def test_parse_facts_flags_non_arm64() -> None:
    facts = parse_facts("boks\nunknown\nx86_64\nUbuntu 24.04 LTS\n4048576\n")
    assert not facts.is_arm64


def test_parse_facts_rejects_truncated_output() -> None:
    with pytest.raises(BootstrapError):
        parse_facts("testvert\nRaspberry Pi 5\naarch64\n")


def test_bootstrap_script_matches_cloud_init_spec() -> None:
    script = render_bootstrap_script("Europe/Oslo", admin_user="kenneth")

    assert script.startswith("#!/bin/sh\nset -eu")
    assert "timedatectl set-timezone Europe/Oslo" in script
    assert "docker.io" in script and "unattended-upgrades" in script
    assert "PasswordAuthentication no" in script
    assert 'Automatic-Reboot "true"' in script
    assert "usermod -aG docker kenneth" in script
    assert "ufw --force enable" in script


def test_bootstrap_script_never_touches_authorized_keys() -> None:
    # Vi kom inn over SSH — nøkkelen virker alt, og scriptet skal ikke kunne låse oss ute.
    script = render_bootstrap_script("Europe/Oslo")
    assert "authorized_keys" not in script
