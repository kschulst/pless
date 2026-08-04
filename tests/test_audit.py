"""Trusselmodell: angriper har allerede LAN-tilgang (wifi, IoT, gjest).
De skal ikke finne en tjeneste å angripe eller få ut dokumenter."""

from pless.audit import (
    Severity,
    analyse,
    check_docker_ports,
    check_encrypted_storage,
    check_listening_sockets,
    check_sshd,
    check_ufw,
    split_sections,
)

SS_ONLY_LOOPBACK = [
    'LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:* users:(("docker-proxy",pid=1,fd=4))',
    "LISTEN 0 4096 [::1]:8000 [::]:*",
]
SS_SSH_OPEN_TO_LAN = [
    'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1017,fd=3))',
]

UFW_LAN_OPEN = [
    "Status: active",
    "Default: deny (incoming), allow (outgoing), deny (routed)",
    "22/tcp (OpenSSH)           ALLOW IN    Anywhere",
]
UFW_HARDENED = [
    "Status: active",
    "Default: deny (incoming), allow (outgoing), deny (routed)",
    "22/tcp on tailscale0       ALLOW IN    Anywhere",
]


class TestListeningSockets:
    def test_loopback_only_passes(self) -> None:
        assert check_listening_sockets(SS_ONLY_LOOPBACK).ok

    def test_ssh_on_all_interfaces_fails(self) -> None:
        finding = check_listening_sockets(SS_SSH_OPEN_TO_LAN)
        assert not finding.ok
        assert "0.0.0.0:22" in finding.detail

    def test_empty_output_passes(self) -> None:
        assert check_listening_sockets([]).ok


class TestUfw:
    def test_inactive_fails(self) -> None:
        assert not check_ufw(["Status: inactive"]).ok

    def test_missing_default_deny_fails(self) -> None:
        finding = check_ufw(["Status: active", "Default: allow (incoming)"])
        assert not finding.ok

    def test_rule_open_to_anywhere_fails(self) -> None:
        finding = check_ufw(UFW_LAN_OPEN)
        assert not finding.ok
        assert "OpenSSH" in finding.detail

    def test_tailscale_bound_rule_passes(self) -> None:
        assert check_ufw(UFW_HARDENED).ok


class TestDockerPorts:
    """Docker skriver iptables-regler forbi UFW — den viktigste fella."""

    def test_loopback_published_passes(self) -> None:
        assert check_docker_ports(["paperless-webserver-1 127.0.0.1:8000->8000/tcp"]).ok

    def test_unpublished_container_ports_pass(self) -> None:
        assert check_docker_ports(["paperless-db-1 5432/tcp"]).ok

    def test_wildcard_publish_fails(self) -> None:
        finding = check_docker_ports(["paperless-webserver-1 0.0.0.0:8000->8000/tcp"])
        assert not finding.ok
        assert "omgår UFW" in finding.detail

    def test_bare_port_publish_fails(self) -> None:
        # `ports: ["8000:8000"]` uten IP-prefiks er nettopp feilen vi vokter mot.
        assert not check_docker_ports(["web 8000->8000/tcp"]).ok

    def test_mixed_mappings_flag_only_the_leaky_one(self) -> None:
        finding = check_docker_ports(
            ["web 127.0.0.1:8000->8000/tcp, 0.0.0.0:9000->9000/tcp", "db 5432/tcp"]
        )
        assert not finding.ok
        assert "9000" in finding.detail and "db" not in finding.detail


class TestSshd:
    def test_password_auth_disabled_passes(self) -> None:
        assert check_sshd(["passwordauthentication no", "permitrootlogin prohibit-password"]).ok

    def test_password_auth_enabled_fails(self) -> None:
        assert not check_sshd(["passwordauthentication yes"]).ok


class TestEncryptedStorage:
    def test_luks_mapper_passes(self) -> None:
        assert check_encrypted_storage(["/dev/mapper/paperless-data"]).ok

    def test_plain_partition_fails(self) -> None:
        finding = check_encrypted_storage(["/dev/sda2"])
        assert not finding.ok
        assert finding.severity == Severity.CRITICAL

    def test_unmounted_is_warning_not_critical(self) -> None:
        # Umontert betyr som regel «låst», som er trygt — ikke et sikkerhetsavvik.
        finding = check_encrypted_storage([])
        assert not finding.ok
        assert finding.severity == Severity.WARNING


def test_split_sections_handles_markers_and_blank_lines() -> None:
    sections = split_sections("##LISTEN\n\nline1\n##UFW\nline2\n##DOCKER\n")
    assert sections["listen"] == ["line1"]
    assert sections["ufw"] == ["line2"]
    assert sections["docker"] == []


def test_analyse_reports_lan_exposure_end_to_end() -> None:
    output = (
        "##LISTEN\n"
        + "\n".join(SS_SSH_OPEN_TO_LAN)
        + "\n##UFW\n"
        + "\n".join(UFW_LAN_OPEN)
        + "\n##DOCKER\nweb 127.0.0.1:8000->8000/tcp\n"
        + "##SSHD\npasswordauthentication no\n"
        + "##MOUNT\n/dev/mapper/paperless-data\n"
    )
    report = analyse(output)
    assert not report.ok
    failed = {f.check for f in report.failures}
    assert failed == {"lyttende sockets", "brannmur"}


def test_analyse_passes_a_fully_hardened_host() -> None:
    output = (
        "##LISTEN\n"
        + "\n".join(SS_ONLY_LOOPBACK)
        + "\n##UFW\n"
        + "\n".join(UFW_HARDENED)
        + "\n##DOCKER\nweb 127.0.0.1:8000->8000/tcp\n"
        + "##SSHD\npasswordauthentication no\n"
        + "##MOUNT\n/dev/mapper/paperless-data\n"
    )
    assert analyse(output).ok
