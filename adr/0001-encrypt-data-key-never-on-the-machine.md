# 0001 — Encrypt the data volume, and never store the key on the machine

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

The archive holds scanned identity documents, financial records and contracts. The most
likely way to lose control of them is not a remote attacker but something physical: the
machine is stolen, or a disk is discarded or returned under warranty without being wiped.

Encryption only helps if the key is not sitting next to the data. Any scheme where the
machine can unlock itself unattended can also be unlocked by whoever carries it away.

## Decision

All Paperless data lives on a LUKS2 volume mounted at `/opt/paperless`. The passphrase is
never written to the machine — not to a key file, not into the initramfs, not to a TPM. It is
supplied over SSH at unlock time and exists only in the operator's head and password manager.

## Consequences

A stolen machine yields hardware and an operating system, not documents. This is the property
the rest of the design is built around.

The cost is accepted deliberately: after every reboot the archive is locked and the stack is
down until someone runs `pless unlock`. See [ADR 0003](0003-manual-unlock-after-boot.md).

A forgotten passphrase means unrecoverable data, by construction. `pless storage init`
therefore tells the operator to save it *before* typing it.

## Alternatives considered

- **Full-disk encryption including the root filesystem:** stronger on paper, but it requires
  unlocking from initramfs over dropbear, which then has to survive every kernel update. More
  moving parts, protecting an operating system that contains nothing secret.
- **Key in a TPM or in a file on disk:** removes the manual step, and removes the guarantee
  with it. A machine that can unlock itself unlocks itself for a thief too.
- **No encryption, rely on physical security:** the threat this project exists to address.
