import yaml

from pless.hostspec import build_user_data, render_user_data

PUBKEY = "ssh-ed25519 AAAAC3Nza test@example"


def test_render_starts_with_cloud_config_header() -> None:
    rendered = render_user_data(PUBKEY, "Europe/Oslo")
    assert rendered.startswith("#cloud-config\n")


def test_rendered_yaml_roundtrips() -> None:
    rendered = render_user_data(PUBKEY, "Europe/Oslo")
    data = yaml.safe_load(rendered)
    assert data == build_user_data(PUBKEY, "Europe/Oslo")


def test_spec_contains_core_hardening() -> None:
    spec = build_user_data(PUBKEY, "Europe/Oslo")

    assert PUBKEY in spec["ssh_authorized_keys"]
    assert spec["timezone"] == "Europe/Oslo"
    assert "docker.io" in spec["packages"]
    assert "unattended-upgrades" in spec["packages"]

    sshd_files = [f for f in spec["write_files"] if "sshd_config.d" in f["path"]]
    assert "PasswordAuthentication no" in sshd_files[0]["content"]

    reboot_files = [f for f in spec["write_files"] if "auto-reboot" in f["path"]]
    assert 'Automatic-Reboot "true"' in reboot_files[0]["content"]

    assert "ufw --force enable" in spec["runcmd"]


def test_admin_user_is_configurable() -> None:
    spec = build_user_data(PUBKEY, "Europe/Oslo", admin_user="pi")
    assert "usermod -aG docker pi" in spec["runcmd"]
