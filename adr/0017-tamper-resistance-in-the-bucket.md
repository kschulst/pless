# 0017 — Tamper resistance lives in the bucket, not in the credential

- **Status:** Accepted
- **Date:** 2026-08-06
- **Context references:** issue #2; amends [0013](0013-backup-offsite-and-drilled.md)

## Context

[ADR 0005](0005-the-local-network-is-hostile.md) assumes the machine can be compromised or
stolen. Whoever has it also has every credential stored on it, so a backup credential with full
access means an attacker deletes the archive and its rescue in one operation. The obvious
answer is a key that can write but not delete: an attacker can then only add.

That answer does not survive contact with restic. restic creates and removes files in `locks/`
during an ordinary backup, and [complains loudly without the right to do
so](https://github.com/restic/restic/issues/3491); `restic unlock` cannot clean up afterwards
either, for the same reason. There are open issues and pull requests, and nothing merged. The
`--no-lock` workaround removes protection against concurrent operations — and this design has a
backup timer and a verification timer running against the same repository.

So the property has to come from somewhere other than the credential.

## Decision

The target holds a Backblaze B2 application key with full read, write and delete on **one
bucket**, and no capability to change bucket settings — no `writeBuckets`, `deleteBuckets`,
`writeBucketRetentions`, `writeKeys` or `deleteKeys`.

The bucket has versioning enabled and a lifecycle rule that retains deleted versions for
`[backup] version_retention_days`, defaulting to 90.

`pless audit` verifies both — the key's capabilities and bucket restriction, and the bucket's
lifecycle window — and fails when either is wrong.

## Consequences

An attacker who takes the machine can delete every snapshot, and the versions survive in an
account they have no access to. restic runs unmodified, and setup is two clicks in B2 plus
three lines of documentation.

The protection is invisible from the machine, and an operator can get the key wrong without
ever noticing. That is why it is a failing audit check rather than a setup instruction nobody
rereads: the whole design rests on a fact about the world, so the fact has to be checked
mechanically.

Retention no longer has to run from the operator's machine. Versioning means a compromised
machine that runs `restic forget --prune` still leaves the configured window of recoverable
versions behind, so `pless backup forget --confirm` runs on the target against the key already
there. It stays an occasional, deliberate act, never a timer — that part of the original
reasoning holds even though its cause is gone.

Tamper resistance is now a property of the storage provider rather than of `pless`. A local
directory repository and rclone-mediated targets get no such guarantee, and the documentation
has to say so plainly rather than presenting the kinds as peers.

Cost is not a factor. B2 is roughly $6/TB/month with the first 10 GB free, and an archive of a
few gigabytes with versioning stays under that. Restores are free too, since egress up to three
times stored volume is included.

## Alternatives considered

- **A key without delete capability:** the original plan, and the reason this record exists. It
  cannot drive restic at all.
- **`restic --no-lock`:** would make a no-delete key work, at the price of concurrency
  protection — with two timers and manual verification hitting the same repository, that is the
  wrong thing to trade away.
- **B2 Object Lock:** a stronger immutability guarantee, and a lock cannot be lifted before it
  expires. A misconfigured retention then binds the operator to paying for data they no longer
  want. Worth documenting as an option for those who want it; wrong as the default.
- **A second cloud provider:** doubles the configuration and the failure modes, not the cost,
  for a scenario `pless backup mirror` already covers.
