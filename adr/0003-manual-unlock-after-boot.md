# 0003 — Unlock manually after every boot

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

[ADR 0001](0001-encrypt-data-key-never-on-the-machine.md) puts the key outside the machine, so
something has to supply it after each boot. Boots are not rare: automatic security updates
reboot the machine when the kernel changes, and power cuts happen.

## Decision

The root filesystem stays unencrypted; only the data volume is encrypted. After boot, an
operator runs `pless unlock`, which opens the volume, mounts it and starts the stack.

`paperless.service` declares `RequiresMountsFor=/opt/paperless`, so systemd refuses to start
the stack against an unmounted volume.

## Consequences

The archive is unavailable between a reboot and the next unlock — in practice once or twice a
month, plus power cuts. Users have to understand this is the system working rather than
failing, which is why it leads the maintenance documentation.

The systemd guard matters more than it appears to. Without it, Paperless would start against
an empty directory and look healthy with no documents in it. Failing loudly is better than
succeeding emptily.

Because the volume is created with a spare key slot, automatic unlocking
([Clevis and Tang](https://github.com/latchset/tang), or something phone-based) can be added
later without re-encrypting anything.

## Alternatives considered

- **Clevis and Tang from the start:** lets the machine unlock itself at home and stay sealed
  anywhere else, which is genuinely attractive. Deferred because it needs a second always-on
  machine, and because a burglar who takes both boxes defeats it.
- **Unattended unlock from a key file:** exactly what ADR 0001 exists to avoid.
