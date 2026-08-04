"""Configuration: pless.toml (non-secret) plus .env and the environment (secrets)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_FILENAME = "pless.toml"


class TargetConfig(BaseModel):
    type: str = "vm"  # "vm" | "pi" | "hetzner"


class VmConfig(BaseModel):
    # "lima": Debian 13 provisioned by SSH bootstrap — mirrors a Pi
    # "multipass": Ubuntu provisioned by cloud-init — mirrors Hetzner
    backend: str = "lima"
    name: str = "pless-dev"
    cpus: int = 2
    memory: str = "4G"
    disk: str = "20G"
    data_size_gb: int = 5  # size of the loop file standing in for the data disk

    @property
    def memory_gb(self) -> int:
        return _parse_size_gb(self.memory)

    @property
    def disk_gb(self) -> int:
        return _parse_size_gb(self.disk)


def _parse_size_gb(value: str) -> int:
    """'4G' / '4GB' / '4' -> 4. Lima wants a number, Multipass wants a string."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError(f"Could not parse size: {value!r}")
    return int(digits)


class PiConfig(BaseModel):
    host: str = ""  # Tailscale name, mDNS name or IP address
    user: str = "ubuntu"
    # "file": a LUKS file on the root filesystem. The default, because both
    # Ubuntu and Raspberry Pi OS grow the root partition to fill the disk on
    # first boot, leaving no free space to partition.
    # "partition": an existing block device, such as a dedicated disk.
    data_mode: str = "file"
    data_size_gb: int = 200  # data_mode="file" only
    data_device: str = ""  # data_mode="partition" only; use /dev/disk/by-id/...


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
    # Which tailnet the machine joins is decided by TS_AUTHKEY — one key
    # belongs to one tailnet. An empty login_server means Tailscale's own
    # control plane; set a Headscale URL for a self-hosted one.
    login_server: str = ""
    hostname: str = ""  # empty means use the machine's own hostname


class PaperlessConfig(BaseModel):
    version: str = "2.20.15"  # exact image tag, bumped deliberately
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
    """Read from the environment and .env.

    Your password manager is the source of truth; .env is a local cache.
    """

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
    """Look for pless.toml in the current directory and upwards, like git."""
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
