"""Konfigurasjon: pless.toml (ikke-hemmelig) + .env/miljø (secrets)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_FILENAME = "pless.toml"


class TargetConfig(BaseModel):
    type: str = "vm"  # "vm" | "pi" | "hetzner"


class VmConfig(BaseModel):
    # "lima": Debian 13 + SSH-bootstrap (speiler Pi/RPi OS Trixie)
    # "multipass": Ubuntu + cloud-init (speiler Hetzner)
    backend: str = "lima"
    name: str = "pless-dev"
    cpus: int = 2
    memory: str = "4G"
    disk: str = "20G"
    data_size_gb: int = 5  # størrelse på loop-fila som simulerer datadisken

    @property
    def memory_gb(self) -> int:
        return _parse_size_gb(self.memory)

    @property
    def disk_gb(self) -> int:
        return _parse_size_gb(self.disk)


def _parse_size_gb(value: str) -> int:
    """'4G' / '4GB' / '4' → 4. Lima vil ha tall, Multipass vil ha streng."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError(f"Kunne ikke tolke størrelse: {value!r}")
    return int(digits)


class PiConfig(BaseModel):
    host: str = ""  # tailscale-navn, mDNS-navn eller IP
    user: str = "ubuntu"
    # "file": LUKS-fil på NVMe-en (Ubuntu auto-grower root til hele disken, så
    # det finnes ingen ledig plass å partisjonere — se beslutning #24).
    # "partition": eksisterende blokk-enhet, f.eks. en egen disk.
    data_mode: str = "file"
    data_size_gb: int = 200  # kun for data_mode="file"
    data_device: str = ""  # kun for data_mode="partition", bruk /dev/disk/by-id/...


class HetznerConfig(BaseModel):
    location: str = "hel1"
    server_type: str = "cx23"
    image: str = "debian-13"
    server_name: str = "paperless-01"


class SshConfig(BaseModel):
    user: str = "root"
    key_path: str = "~/.ssh/id_ed25519"

    @property
    def key(self) -> Path:
        return Path(self.key_path).expanduser()


class AccessConfig(BaseModel):
    mode: str = "tailscale"  # "tailscale" | "ssh-tunnel"


class TailscaleConfig(BaseModel):
    # Hvilket tailnet boksen blir med i avgjøres av TS_AUTHKEY — én nøkkel
    # hører til ett tailnet. Tom login_server = Tailscales egen kontrollplan;
    # sett den til en Headscale-URL for selvhostet nett.
    login_server: str = ""
    hostname: str = ""  # tom = bruk maskinens eget vertsnavn


class PaperlessConfig(BaseModel):
    version: str = "2.20.15"  # eksakt image-tag; bumpes bevisst (senere: `pless update`)
    timezone: str = "Europe/Oslo"
    ocr_languages: str = "nor+eng"
    admin_user: str = "admin"


class PathsConfig(BaseModel):
    local_documents: str = "./documents"
    local_backups: str = "./backups"

    @property
    def documents(self) -> Path:
        return Path(self.local_documents).expanduser()

    @property
    def backups(self) -> Path:
        return Path(self.local_backups).expanduser()


class StorageConfig(BaseModel):
    min_free_gb_after_upload: int = 10
    max_disk_usage_percent_after_upload: int = 70


class BackupConfig(BaseModel):
    restic_repository: str = ""


class Config(BaseModel):
    target: TargetConfig = TargetConfig()
    tailscale: TailscaleConfig = TailscaleConfig()
    vm: VmConfig = VmConfig()
    pi: PiConfig = PiConfig()
    hetzner: HetznerConfig = HetznerConfig()
    ssh: SshConfig = SshConfig()
    access: AccessConfig = AccessConfig()
    paperless: PaperlessConfig = PaperlessConfig()
    paths: PathsConfig = PathsConfig()
    storage: StorageConfig = StorageConfig()
    backup: BackupConfig = BackupConfig()


class Secrets(BaseSettings):
    """Leses fra miljø og .env. Bitwarden er source of truth; .env er cache."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    hcloud_token: str = ""
    paperless_api_token: str = ""
    paperless_admin_password: str = ""
    paperless_secret_key: str = ""
    postgres_password: str = ""
    restic_password: str = ""
    b2_account_id: str = ""
    b2_account_key: str = ""
    ts_authkey: str = ""


def find_config_file(start: Path | None = None) -> Path | None:
    """Let etter pless.toml i cwd og oppover — samme modell som git."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None) -> Config:
    config_path = path or find_config_file()
    if config_path is None:
        return Config()
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def load_secrets() -> Secrets:
    return Secrets()
