"""Configuration: pless.toml (non-secret) plus .env and the environment (secrets).

`pless` configures and operates one thing: a host reachable over SSH. How that
host came into existence — a Raspberry Pi you flashed, a VM on your hypervisor,
a droplet you clicked into being, a machine `pless vm create` made for you — is
a separate, optional concern. Only `[host]` describes what the tool needs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_FILENAME = "pless.toml"


class HostConfig(BaseModel):
    """How to reach the machine. Everything else follows from this.

    Two ways to say it. Either the connection details directly:

        address = "archive.local"
        user = "admin"

    Or a reference to an SSH config file, which is what `pless vm create`
    writes and what you want if your setup already involves a bastion, a
    non-standard port, or anything else your ~/.ssh/config knows about:

        ssh_config = "~/.lima/pless-dev/ssh.config"
        ssh_alias = "lima-pless-dev"
    """

    address: str = ""
    user: str = "root"
    key_path: str = "~/.ssh/id_ed25519"
    port: int = 22

    ssh_config: str = ""
    ssh_alias: str = ""

    @property
    def key(self) -> Path:
        return Path(self.key_path).expanduser()

    @property
    def uses_ssh_config(self) -> bool:
        return bool(self.ssh_config and self.ssh_alias)

    @property
    def is_configured(self) -> bool:
        return self.uses_ssh_config or bool(self.address)

    @property
    def label(self) -> str:
        """A short name for output, so people can tell machines apart."""
        if self.uses_ssh_config:
            return self.ssh_alias
        return self.address or "(unconfigured)"


class StorageConfig(BaseModel):
    # "file": a LUKS file on the root filesystem. The default, because both
    # Ubuntu and Raspberry Pi OS grow the root partition to fill the disk on
    # first boot, leaving no free space to partition.
    # "partition": an existing block device, such as a dedicated disk.
    data_mode: str = "file"
    data_size_gb: int = 100  # data_mode="file" only
    data_device: str = ""  # data_mode="partition" only; use /dev/disk/by-id/...

    min_free_gb_after_upload: int = 10
    max_disk_usage_percent_after_upload: int = 70


class VmConfig(BaseModel):
    """Optional: let pless create a local VM for you.

    Not a kind of host — a way to obtain one. After `pless vm create`, the
    machine is reached through `[host]` like any other.
    """

    # "lima": Debian provisioned by SSH bootstrap — mirrors a Pi
    # "multipass": Ubuntu provisioned by cloud-init — mirrors a cloud server
    backend: str = "lima"
    name: str = "pless-dev"
    cpus: int = 2
    memory: str = "4G"
    disk: str = "20G"

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


class HetznerConfig(BaseModel):
    """Optional: let pless create a Hetzner Cloud server for you.

    Every other provider works too — create the machine however you like and
    point `[host]` at it. This section only saves you that step.
    """

    location: str = "hel1"
    server_type: str = "cx23"
    image: str = "debian-13"
    server_name: str = "paperless-01"


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


class BackupConfig(BaseModel):
    """Where snapshots go, how often, and how much history survives.

    The repository is an opaque restic location, not a typed backend. restic
    already speaks local paths, S3/B2 and — through rclone — much else, so
    treating it as a string keeps pless out of the storage-backend business.
    """

    restic_repository: str = ""  # empty disables backup

    schedule: str = "daily"  # systemd OnCalendar for the backup timer
    verify_schedule: str = "weekly"  # systemd OnCalendar for content verification
    verify_sample_size: int = 20  # files hashed per content verification
    verify_max_age_days: int = 14  # older than this and preflight says "not verified"

    quiescence_timeout_seconds: int = 900  # how long to wait for the task queue to drain

    retention_daily: int = 7
    retention_weekly: int = 8
    retention_monthly: int = 12
    retention_yearly: int = 3

    # What the bucket's lifecycle rule is expected to keep. pless audits this;
    # it never applies it, because a key that could would defeat the point.
    version_retention_days: int = 90

    exporter_delete: bool = False  # pass --delete to document_exporter

    @property
    def is_configured(self) -> bool:
        return bool(self.restic_repository)

    @property
    def repository_kind(self) -> str:
        """'b2', 's3', 'local'… — the kind, never the location.

        Used where naming the location would put a bucket name somewhere it
        does not belong, and to say plainly that a local repository protects
        against deletion and corruption but not against losing the machine.
        """
        if not self.restic_repository:
            return "unset"
        scheme, separator, _ = self.restic_repository.partition(":")
        # A Windows-style drive letter is not a scheme, and neither is a path.
        if separator and scheme and scheme.isalnum() and len(scheme) > 1:
            return scheme.lower()
        return "local"

    @property
    def is_local_repository(self) -> bool:
        return self.repository_kind == "local"


class Config(BaseModel):
    host: HostConfig = HostConfig()
    storage: StorageConfig = StorageConfig()
    tailscale: TailscaleConfig = TailscaleConfig()
    access: AccessConfig = AccessConfig()
    paperless: PaperlessConfig = PaperlessConfig()
    paths: PathsConfig = PathsConfig()
    backup: BackupConfig = BackupConfig()

    # Optional ways to obtain a host, rather than kinds of host.
    vm: VmConfig = VmConfig()
    hetzner: HetznerConfig = HetznerConfig()


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
