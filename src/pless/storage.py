"""The encrypted data volume on the target: init, unlock, lock, status.

All Paperless data lives on a LUKS2 volume mounted at /opt/paperless. The key
is never stored on the machine — the passphrase arrives on stdin at unlock
time. On a VM, and by default on a Pi, the block device is a sparse file
attached as a loop device.
"""

from __future__ import annotations

from dataclasses import dataclass

from pless import config, sshexec
from pless.targets import TargetHost

DATA_IMG = "/var/lib/pless/data.img"
MAPPER_NAME = "paperless-data"
MAPPER_DEV = f"/dev/mapper/{MAPPER_NAME}"
MOUNTPOINT = "/opt/paperless"


class StorageError(RuntimeError):
    pass


def detect_cipher_args(cpuinfo: str) -> list[str]:
    """Pick the LUKS cipher from /proc/cpuinfo.

    CPUs with AES instructions (Pi 5, x86) get AES-XTS at full hardware speed.
    The Pi 4 and older lack them, because Broadcom did not license the ARM
    crypto extensions, so they get Adiantum — the cipher Google designed for
    exactly this situation.
    """

    def line_has_aes(line: str) -> bool:
        key, _, value = line.partition(":")
        return key.strip().lower() in ("features", "flags") and "aes" in value.lower().split()

    if any(line_has_aes(line) for line in cpuinfo.splitlines()):
        return ["--cipher", "aes-xts-plain64", "--key-size", "512"]
    return ["--cipher", "xchacha20,aes-adiantum-plain64", "--key-size", "256"]


# Finds — and on first use creates — the block device backing the data volume,
# writing its path to stdout. Loop devices do not survive a reboot, so this
# runs on both init and unlock.
_ENSURE_LOOP_DEVICE = f"""\
set -eu
sudo mkdir -p /var/lib/pless
if [ ! -f {DATA_IMG} ]; then
  sudo truncate -s {{size_gb}}G {DATA_IMG}
fi
DEV=$(sudo losetup -j {DATA_IMG} | cut -d: -f1)
if [ -z "$DEV" ]; then
  DEV=$(sudo losetup --find --show {DATA_IMG})
fi
echo "$DEV"
"""


@dataclass
class StorageStatus:
    device: str
    is_luks: bool
    is_open: bool
    is_mounted: bool


def _run(target: TargetHost, command: str, input_text: str | None = None) -> sshexec.SshResult:
    return sshexec.run(
        target.user,
        target.host,
        target.key,
        command,
        timeout=120,
        input_text=input_text,
        port=target.port,
    )


def _run_ok(target: TargetHost, command: str, input_text: str | None = None) -> str:
    result = _run(target, command, input_text)
    if not result.ok:
        raise StorageError(
            f"Remote command failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def resolve_data_device(cfg: config.Config, target: TargetHost) -> str:
    if cfg.target.type == "vm":
        return _run_ok(target, _ENSURE_LOOP_DEVICE.format(size_gb=cfg.vm.data_size_gb))
    if cfg.target.type == "pi":
        if cfg.pi.data_mode == "file":
            return _run_ok(target, _ENSURE_LOOP_DEVICE.format(size_gb=cfg.pi.data_size_gb))
        if not cfg.pi.data_device:
            raise StorageError(
                '[pi] data_mode = "partition" requires data_device to be set in pless.toml.'
            )
        return cfg.pi.data_device
    raise StorageError(f"Encrypted storage is not supported for target {cfg.target.type!r} yet.")


def status(cfg: config.Config, target: TargetHost) -> StorageStatus:
    device = resolve_data_device(cfg, target)
    is_luks = _run(target, f"sudo cryptsetup isLuks {device}").ok
    is_open = _run(target, f"sudo cryptsetup status {MAPPER_NAME} >/dev/null 2>&1").ok
    is_mounted = _run(target, f"mountpoint -q {MOUNTPOINT}").ok
    return StorageStatus(device=device, is_luks=is_luks, is_open=is_open, is_mounted=is_mounted)


def init(cfg: config.Config, target: TargetHost, passphrase: str) -> str:
    """Format the data volume as LUKS2 with ext4 and mount it. Destructive."""
    device = resolve_data_device(cfg, target)
    if _run(target, f"sudo cryptsetup isLuks {device}").ok:
        raise StorageError(f"{device} is already LUKS-formatted — init refuses to overwrite it.")

    cpuinfo = _run_ok(target, "cat /proc/cpuinfo")
    cipher_str = " ".join(detect_cipher_args(cpuinfo))

    _run_ok(
        target,
        f"sudo cryptsetup luksFormat --type luks2 {cipher_str} --batch-mode --key-file=- {device}",
        input_text=passphrase,
    )
    _run_ok(
        target,
        f"sudo cryptsetup open --key-file=- {device} {MAPPER_NAME}",
        input_text=passphrase,
    )
    _run_ok(target, f"sudo mkfs.ext4 -q -L paperless {MAPPER_DEV}")
    _run_ok(target, f"sudo mkdir -p {MOUNTPOINT} && sudo mount {MAPPER_DEV} {MOUNTPOINT}")
    return device


def unlock(cfg: config.Config, target: TargetHost, passphrase: str) -> StorageStatus:
    device = resolve_data_device(cfg, target)
    current = status(cfg, target)
    if not current.is_luks:
        raise StorageError(f"{device} is not LUKS-formatted — run `pless storage init` first.")
    if not current.is_open:
        _run_ok(
            target,
            f"sudo cryptsetup open --key-file=- {device} {MAPPER_NAME}",
            input_text=passphrase,
        )
    if not current.is_mounted:
        _run_ok(target, f"sudo mkdir -p {MOUNTPOINT} && sudo mount {MAPPER_DEV} {MOUNTPOINT}")
    # Deploy installs paperless.service with RequiresMountsFor=/opt/paperless.
    # Start it if it exists; otherwise this is a no-op.
    _run(target, "sudo systemctl start paperless.service 2>/dev/null || true")
    return status(cfg, target)


def lock(cfg: config.Config, target: TargetHost) -> StorageStatus:
    _run(target, "sudo systemctl stop paperless.service 2>/dev/null || true")
    current = status(cfg, target)
    if current.is_mounted:
        _run_ok(target, f"sudo umount {MOUNTPOINT}")
    if current.is_open:
        _run_ok(target, f"sudo cryptsetup close {MAPPER_NAME}")
    return status(cfg, target)
