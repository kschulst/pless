# 0010 — Put the data in an encrypted file, not a partition

- **Status:** Accepted
- **Date:** 2026-08-01
- **Supersedes:** an earlier design assuming two separate physical media

## Context

The original plan was an SD card for the operating system and a separate USB stick for the
encrypted data, so that one medium failing could not take the other. Then the hardware
arrived as a single NVMe drive, and the plan met reality:

Both Ubuntu and Raspberry Pi OS grow the root partition to fill the disk on first boot, so
after installation there is no free space left to partition. ext4 cannot be shrunk while
mounted, and shrinking the root filesystem of a running system is not a step to put in a
getting-started guide.

The two-media argument also inverted on inspection. A USB stick is far less reliable than an
NVMe drive, so moving documents onto one to "protect" them moves them somewhere more likely
to fail.

## Decision

The encrypted volume is a sparse file on the root filesystem, attached as a loop device. A
fixed size caps how large it can grow. `data_mode = "partition"` remains available for a
dedicated block device.

## Consequences

A fixed-size file provides the same isolation a partition would: the archive cannot grow into
the operating system's space.

The tool becomes indifferent to storage medium — NVMe, USB SSD, microSD are the same code
path. That lowers the hardware requirements for anyone else adopting it, which serves the
goal of being installable by other people.

Sizing matters and is easy to get wrong. A file sized close to the disk means a full archive
fills the root filesystem, and the ext4 filesystem *inside* LUKS starts failing writes — worse
than an ordinary full disk, because the corruption is a layer down. The configuration
reference gives the arithmetic and errs low, since growing is easy and shrinking is not.

Loop devices do not survive a reboot, so `pless` reattaches on `unlock` and `storage status`.

## Alternatives considered

- **Shrink the root partition and create a real partition:** requires an offline resize, and
  is a hostile first-run experience.
- **Keep two physical media:** the original plan, defeated by the hardware and by the
  reliability argument running the wrong way.
