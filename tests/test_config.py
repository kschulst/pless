from pathlib import Path

from pless.config import Config, find_config_file, load_config


def test_defaults_without_file() -> None:
    cfg = Config()
    assert cfg.hetzner.location == "hel1"
    assert cfg.hetzner.server_type == "cx23"
    assert cfg.access.mode == "tailscale"
    assert cfg.storage.min_free_gb_after_upload == 10


def test_load_partial_toml_keeps_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "pless.toml"
    config_file.write_text('[hetzner]\nserver_name = "test-01"\n')

    cfg = load_config(config_file)

    assert cfg.hetzner.server_name == "test-01"
    assert cfg.hetzner.location == "hel1"  # default is kept
    assert cfg.paperless.ocr_languages == "nor+eng"


def test_find_config_walks_upwards(tmp_path: Path) -> None:
    (tmp_path / "pless.toml").write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    found = find_config_file(start=nested)

    assert found == tmp_path / "pless.toml"


def test_find_config_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_config_file(start=tmp_path) is None


def test_ssh_key_path_expands_home(tmp_path: Path) -> None:
    config_file = tmp_path / "pless.toml"
    config_file.write_text('[ssh]\nkey_path = "~/.ssh/test_key"\n')
    cfg = load_config(config_file)
    assert "~" not in str(cfg.ssh.key)
