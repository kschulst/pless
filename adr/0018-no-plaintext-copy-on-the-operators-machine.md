# 0018 — A plaintext local copy is not a backup layer

- **Status:** Accepted
- **Date:** 2026-08-06
- **Context references:** issue #2; amends [0013](0013-backup-offsite-and-drilled.md)

## Context

[ADR 0013](0013-backup-offsite-and-drilled.md) describes two layers, the first being an export
"pulled down to the operator's machine". The argument was insurance: `RESTIC_PASSWORD` has no
recovery path, so a readable copy somewhere else means losing it is survivable.

That argument is wrong twice over.

It puts readable documents on a laptop, which undermines the guarantee the entire design exists
to provide. The volume is encrypted, the snapshots are encrypted, access goes through Tailscale
— and then a scheduled command writes the whole archive in the clear to the least protected
machine involved.

And it hedges against a failure mode already accepted deliberately elsewhere.
[ADR 0001](0001-encrypt-data-key-never-on-the-machine.md) says the LUKS passphrase lives in a
password manager and losing it means losing the data. Treating `RESTIC_PASSWORD` differently
would weaken the primary guarantee in order to soften a secondary one.

## Decision

No routine plaintext copy. Two deliberate operations replace it:

- **`pless backup extract`** — pull documents out of a snapshot to a local directory, in the
  clear. An inspection and escape-hatch tool: on demand, `--confirm` required, never scheduled,
  and it names the destination and says what it is leaving on disk before writing anything.
- **`pless backup mirror`** — copy the repository to another location with `restic copy`, still
  encrypted end to end. This is the answer to "what if the B2 account is lost".

`pless backup download`, listed in issue #2's original scope, is folded into `extract`, whose
default destination is `[paths] local_backups`.

## Consequences

Readable documents leave the encrypted volume only when the operator asks for them, and the
command says out loud what it is doing.

The answer to losing an account is a mirror, not plaintext — a second encrypted copy has the
same confidentiality property as the first, so adding one costs nothing but disk.

The answer to losing `RESTIC_PASSWORD` is that the backup is gone. Both passphrases live in a
password manager, both are unrecoverable, and the documentation says so as loudly for one as
for the other. Consistency here is the point.

An escape hatch still exists, which matters: an archive nobody can inspect without a full
restore is an archive people quietly stop trusting.

## Alternatives considered

- **Keep the scheduled download from ADR 0013:** the option this record removes. Convenient,
  and it makes the weakest machine in the system hold the most readable copy.
- **No local extraction at all:** the smallest surface, and it leaves the operator unable to get
  a single document out without restoring everything.
- **Extract to an encrypted disk image locally:** a third encryption scheme, a third passphrase,
  and a fourth thing to lose. `mirror` already provides an encrypted second copy.
