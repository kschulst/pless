"""Genererer docker-compose.yml og server-side .env for Paperless-stacken.

Alt persistent bor under /opt/paperless (LUKS-montert). Webserveren bindes
KUN til 127.0.0.1 på targetet (beslutning #3) — tilgang via SSH-tunnel eller
tailscale serve. Secrets går i server-.env (på kryptert disk) og refereres
med ${}-interpolasjon i compose-fila.
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
Description=Paperless-ngx compose-stack (pless)
Requires=docker.service
After=docker.service
# Nekter å starte hvis LUKS-disken ikke er montert — `pless unlock` starter oss.
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
    """Kun secrets — resten står eksplisitt i compose-fila. Havner på kryptert disk."""
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
            f"Mangler secrets i lokal .env: {', '.join(missing)} — kjør `pless init --secrets`."
        )
    return (
        f"POSTGRES_PASSWORD={secrets.postgres_password}\n"
        f"PAPERLESS_SECRET_KEY={secrets.paperless_secret_key}\n"
        f"PAPERLESS_ADMIN_PASSWORD={secrets.paperless_admin_password}\n"
    )
