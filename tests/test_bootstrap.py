import pytest

from pless.bootstrap import BootstrapError, parse_facts
from pless.hostspec import render_bootstrap_script


def facts_output(
    hostname: str = "testvert",
    model: str = "Raspberry Pi 5 Model B Rev 1.0",
    arch: str = "aarch64",
    distro_id: str = "debian",
    distro_version: str = "13",
    os_name: str = "Debian GNU/Linux 13 (trixie)",
    mem_kb: int = 8244480,
) -> str:
    return (
        f"hostname={hostname}\n"
        f"model={model}\n"
        f"arch={arch}\n"
        f"distro_id={distro_id}\n"
        f"distro_version={distro_version}\n"
        f"os_name={os_name}\n"
        f"mem_kb={mem_kb}\n"
    )


def test_parse_facts_reads_all_fields() -> None:
    facts = parse_facts(facts_output())
    assert facts.hostname == "testvert"
    assert facts.model == "Raspberry Pi 5 Model B Rev 1.0"
    assert facts.architecture == "aarch64"
    assert facts.distro_id == "debian"
    assert facts.distro_version == "13"
    assert facts.memory_gb == 7.9
    assert facts.is_arm64
    assert facts.is_raspberry_pi


def test_any_hostname_is_accepted() -> None:
    # Verktøyet skal ikke ha meninger om hva boksen heter.
    for name in ("paperless", "testvert", "arkiv-01", "pi"):
        assert parse_facts(facts_output(hostname=name)).hostname == name


def test_field_order_does_not_matter() -> None:
    # Nøkkel=verdi nettopp for å tåle at rekkefølgen eller et ekstra felt endrer seg.
    scrambled = "\n".join(reversed(facts_output().strip().splitlines()))
    facts = parse_facts(scrambled + "\nekstra_felt=noe\n")
    assert facts.hostname == "testvert"
    assert facts.architecture == "aarch64"


def test_quoted_os_release_values_are_stripped() -> None:
    # /etc/os-release siterer gjerne verdiene: VERSION_ID="13"
    facts = parse_facts(facts_output(distro_id='"debian"', distro_version='"13"'))
    assert facts.distro_id == "debian"
    assert facts.distro_version == "13"


def test_parse_facts_flags_non_arm64() -> None:
    facts = parse_facts(facts_output(arch="x86_64", model="ukjent"))
    assert not facts.is_arm64
    assert not facts.is_raspberry_pi


def test_parse_facts_rejects_output_missing_required_fields() -> None:
    with pytest.raises(BootstrapError, match="Mangler fakta"):
        parse_facts("hostname=testvert\narch=aarch64\n")


class TestDistroSupport:
    """Debian og Ubuntu er likestilt (beslutning: begge testes)."""

    def test_debian_13_supported(self) -> None:
        assert parse_facts(facts_output(distro_id="debian", distro_version="13")).distro_supported

    def test_ubuntu_2404_supported(self) -> None:
        facts = parse_facts(facts_output(distro_id="ubuntu", distro_version="24.04"))
        assert facts.distro_supported

    def test_ubuntu_2604_supported(self) -> None:
        facts = parse_facts(facts_output(distro_id="ubuntu", distro_version="26.04"))
        assert facts.distro_supported

    def test_debian_12_rejected(self) -> None:
        # Bookworm mangler docker-compose-v2 i apt — ville krevd Dockers eget repo.
        facts = parse_facts(facts_output(distro_id="debian", distro_version="12"))
        assert not facts.distro_supported

    def test_ubuntu_2204_rejected(self) -> None:
        facts = parse_facts(facts_output(distro_id="ubuntu", distro_version="22.04"))
        assert not facts.distro_supported

    def test_unknown_distro_rejected(self) -> None:
        facts = parse_facts(facts_output(distro_id="fedora", distro_version="42"))
        assert not facts.distro_supported


def test_bootstrap_script_matches_cloud_init_spec() -> None:
    script = render_bootstrap_script("Europe/Oslo", admin_user="kenneth")

    assert script.startswith("#!/bin/sh\nset -eu")
    assert "timedatectl set-timezone Europe/Oslo" in script
    assert "docker.io" in script and "unattended-upgrades" in script
    assert "PasswordAuthentication no" in script
    assert 'Automatic-Reboot "true"' in script
    assert "usermod -aG docker kenneth" in script
    assert "ufw --force enable" in script


def test_bootstrap_script_is_identical_across_supported_distros() -> None:
    # Pakkenavnene er like på Debian 13 og Ubuntu 24.04+; divergerer de senere,
    # skal denne testen feile og tvinge frem en bevisst gaffel i packages_for().
    assert render_bootstrap_script("Europe/Oslo", "u", "debian") == render_bootstrap_script(
        "Europe/Oslo", "u", "ubuntu"
    )


def test_bootstrap_script_never_touches_authorized_keys() -> None:
    # Vi kom inn over SSH — nøkkelen virker alt, og scriptet skal ikke kunne låse oss ute.
    script = render_bootstrap_script("Europe/Oslo")
    assert "authorized_keys" not in script
