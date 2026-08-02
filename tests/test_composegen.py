import pytest
import yaml

from pless.composegen import (
    SYSTEMD_UNIT,
    build_compose,
    render_compose,
    render_server_env,
)
from pless.config import Config, Secrets


def make_secrets(**overrides: str) -> Secrets:
    values = {
        "postgres_password": "pg-secret",
        "paperless_secret_key": "key-secret",
        "paperless_admin_password": "admin-secret",
    }
    values.update(overrides)
    return Secrets(_env_file=None, **values)


def test_compose_renders_valid_yaml_roundtrip() -> None:
    cfg = Config()
    assert yaml.safe_load(render_compose(cfg)) == build_compose(cfg)


def test_webserver_binds_only_to_localhost() -> None:
    web = build_compose(Config())["services"]["webserver"]
    assert web["ports"] == ["127.0.0.1:8000:8000"]


def test_secrets_are_interpolated_not_inlined() -> None:
    rendered = render_compose(Config())
    assert "${POSTGRES_PASSWORD}" in rendered
    assert "${PAPERLESS_SECRET_KEY}" in rendered
    assert "${PAPERLESS_ADMIN_PASSWORD}" in rendered


def test_ocr_language_from_config() -> None:
    cfg = Config()  # default nor+eng
    env = build_compose(cfg)["services"]["webserver"]["environment"]
    assert env["PAPERLESS_OCR_LANGUAGE"] == "nor+eng"
    assert env["PAPERLESS_OCR_LANGUAGES"] == "nor eng"


def test_tika_and_gotenberg_wired() -> None:
    services = build_compose(Config())["services"]
    env = services["webserver"]["environment"]
    assert env["PAPERLESS_TIKA_ENABLED"] == "1"
    assert env["PAPERLESS_TIKA_ENDPOINT"] == "http://tika:9998"
    assert "tika" in services and "gotenberg" in services


def test_server_env_contains_all_secrets() -> None:
    env = render_server_env(make_secrets())
    assert "POSTGRES_PASSWORD=pg-secret" in env
    assert "PAPERLESS_SECRET_KEY=key-secret" in env
    assert "PAPERLESS_ADMIN_PASSWORD=admin-secret" in env


def test_server_env_fails_on_missing_secret() -> None:
    with pytest.raises(ValueError, match="PAPERLESS_SECRET_KEY"):
        render_server_env(make_secrets(paperless_secret_key=""))


def test_systemd_unit_gates_on_mount() -> None:
    assert "RequiresMountsFor=/opt/paperless" in SYSTEMD_UNIT
    assert "docker compose up -d" in SYSTEMD_UNIT
