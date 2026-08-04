"""Generates docker-compose.yml and the server-side .env for the stack.

Everything persistent lives under /opt/paperless, on the encrypted volume.
The web server binds to 127.0.0.1 on the target only — access goes through an
SSH tunnel or Tailscale. Secrets live in the server-side .env on the encrypted
volume and are referenced with ${} interpolation from the compose file.
"""

from __future__ import annotations

import yaml

from pless import config

PAPERLESS_IMAGE_REPO = "ghcr.io/paperless-ngx/paperless-ngx"
POSTGRES_IMAGE = "docker.io/library/postgres:16"
REDIS_IMAGE = "docker.io/library/redis:7"
GOTENBERG_IMAGE = "docker.io/gotenberg/gotenberg:8"
TIKA_IMAGE = "docker.io/apache/tika:latest"

INSTALL_DIR = "/opt/paperless"
WEB_PORT = 8000

SYSTEMD_UNIT = f"""\
[Unit]
Description=Paperless-ngx compose stack (pless)
Requires=docker.service
After=docker.service
# Refuses to start unless the encrypted volume is mounted; `pless unlock` starts us.
RequiresMountsFor={INSTALL_DIR}

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={INSTALL_DIR}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
"""


def build_compose(cfg: config.Config) -> dict:
    ocr_language = cfg.paperless.ocr_languages  # f.eks. "nor+eng"
    ocr_install = ocr_language.replace("+", " ")

    return {
        "services": {
            "broker": {
                "image": REDIS_IMAGE,
                "restart": "unless-stopped",
                "volumes": ["./redis:/data"],
            },
            "db": {
                "image": POSTGRES_IMAGE,
                "restart": "unless-stopped",
                "volumes": ["./postgres:/var/lib/postgresql/data"],
                "environment": {
                    "POSTGRES_DB": "paperless",
                    "POSTGRES_USER": "paperless",
                    "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}",
                },
            },
            "gotenberg": {
                "image": GOTENBERG_IMAGE,
                "restart": "unless-stopped",
                "command": [
                    "gotenberg",
                    "--chromium-disable-javascript=true",
                    "--chromium-allow-list=file:///tmp/.*",
                ],
            },
            "tika": {
                "image": TIKA_IMAGE,
                "restart": "unless-stopped",
            },
            "webserver": {
                "image": f"{PAPERLESS_IMAGE_REPO}:{cfg.paperless.version}",
                "restart": "unless-stopped",
                "depends_on": ["db", "broker", "gotenberg", "tika"],
                "ports": [f"127.0.0.1:{WEB_PORT}:8000"],
                "volumes": [
                    "./data:/usr/src/paperless/data",
                    "./media:/usr/src/paperless/media",
                    "./export:/usr/src/paperless/export",
                    "./consume:/usr/src/paperless/consume",
                ],
                "environment": {
                    "PAPERLESS_REDIS": "redis://broker:6379",
                    "PAPERLESS_DBHOST": "db",
                    "PAPERLESS_DBNAME": "paperless",
                    "PAPERLESS_DBUSER": "paperless",
                    "PAPERLESS_DBPASS": "${POSTGRES_PASSWORD}",
                    "PAPERLESS_SECRET_KEY": "${PAPERLESS_SECRET_KEY}",
                    "PAPERLESS_ADMIN_USER": cfg.paperless.admin_user,
                    "PAPERLESS_ADMIN_PASSWORD": "${PAPERLESS_ADMIN_PASSWORD}",
                    "PAPERLESS_TIME_ZONE": cfg.paperless.timezone,
                    "PAPERLESS_OCR_LANGUAGE": ocr_language,
                    "PAPERLESS_OCR_LANGUAGES": ocr_install,
                    "PAPERLESS_TIKA_ENABLED": "1",
                    "PAPERLESS_TIKA_ENDPOINT": "http://tika:9998",
                    "PAPERLESS_TIKA_GOTENBERG_ENDPOINT": "http://gotenberg:3000",
                    "PAPERLESS_FILENAME_FORMAT": "{created_year}/{correspondent}/{title}",
                    "PAPERLESS_CONSUMER_RECURSIVE": "true",
                    "USERMAP_UID": "1000",
                    "USERMAP_GID": "1000",
                },
            },
        },
    }


def render_compose(cfg: config.Config) -> str:
    return yaml.safe_dump(build_compose(cfg), sort_keys=False, width=120)


def render_server_env(secrets: config.Secrets) -> str:
    """Secrets only; everything else is explicit in the compose file."""
    missing = [
        name
        for name, value in (
            ("POSTGRES_PASSWORD", secrets.postgres_password),
            ("PAPERLESS_SECRET_KEY", secrets.paperless_secret_key),
            ("PAPERLESS_ADMIN_PASSWORD", secrets.paperless_admin_password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing secrets in your local .env: {', '.join(missing)}. Run `pless init --secrets`."
        )
    return (
        f"POSTGRES_PASSWORD={secrets.postgres_password}\n"
        f"PAPERLESS_SECRET_KEY={secrets.paperless_secret_key}\n"
        f"PAPERLESS_ADMIN_PASSWORD={secrets.paperless_admin_password}\n"
    )
