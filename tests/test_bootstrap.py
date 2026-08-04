import pytest

from pless.bootstrap import BootstrapError, parse_facts
from pless.hostspec import packages_for, render_bootstrap_script


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


def test_docker_packages_differ_between_distros() -> None:
    # Verifisert empirisk på ekte VM-er, ikke antatt fra dokumentasjon.
    debian = packages_for("debian")
    ubuntu = packages_for("ubuntu")

    # Debian: Compose v2 heter «docker-compose»; «docker-compose-v2» finnes ikke.
    assert "docker-compose" in debian
    assert "docker-compose-v2" not in debian
    # Debian skiller ut klienten, og den er kun en Recommends — vi bruker
    # --no-install-recommends, så uten denne får man daemon uten `docker`.
    assert "docker-cli" in debian

    # Ubuntu: motsatt navn, og klienten følger med docker.io.
    assert "docker-compose-v2" in ubuntu
    assert "docker-compose" not in ubuntu

    assert "docker.io" in debian and "docker.io" in ubuntu


def test_unknown_distro_has_no_guessed_docker_packages() -> None:
    with pytest.raises(ValueError, match="docker-pakker"):
        packages_for("fedora")


def test_bootstrap_script_waits_for_apt_lock() -> None:
    # En fersk maskin kjører cloud-init/unattended-upgrades ved første boot;
    # uten venting feiler bootstrap med «Could not get lock».
    script = render_bootstrap_script("Europe/Oslo")
    assert "cloud-init status --wait" in script
    assert "DPkg::Lock::Timeout=300" in script
    # Ingen bare apt-get-kall utenom via $APT-variabelen.
    assert "\napt-get " not in script


def test_bootstrap_disables_llmnr_but_keeps_mdns() -> None:
    # LLMNR (5355) lytter på alle interfacer og er en kjent forgiftningsvektor.
    # mDNS (5353) må overleve — «vertsnavn.local» er dokumentert i oppsettet.
    script = render_bootstrap_script("Europe/Oslo")
    assert "LLMNR=no" in script
    assert "MulticastDNS=no" not in script


def test_bootstrap_script_never_touches_authorized_keys() -> None:
    # Vi kom inn over SSH — nøkkelen virker alt, og scriptet skal ikke kunne låse oss ute.
    script = render_bootstrap_script("Europe/Oslo")
    assert "authorized_keys" not in script
