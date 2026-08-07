# 0017 — A credential the machine holds cannot protect the archive from the machine

- **Status:** Proposed — the constraint is established; the mechanism is not chosen
- **Date:** 2026-08-06
- **Context references:** issues #2 and #14; amends [0013](0013-backup-offsite-and-drilled.md)

## Context

[ADR 0005](0005-the-local-network-is-hostile.md) assumes the machine can be compromised or
stolen. Whoever has it also has every credential stored on it, so a backup credential with full
access means an attacker deletes the archive and its rescue in one operation.

Two candidate answers have now been tested against reality, and both fail.

**A key that cannot delete.** The obvious answer: let the machine write and never remove. It
does not survive contact with restic, which creates and removes files in `locks/` during an
ordinary backup and [complains loudly without the right to do
so](https://github.com/restic/restic/issues/3491). `restic unlock` cannot clean up afterwards
either. The `--no-lock` workaround removes protection against concurrent operations, and this
design runs a backup timer and a verification timer against the same repository.

**Versioning plus a lifecycle rule.** The next answer, and the one this record originally
claimed: give the machine `deleteFiles`, enable bucket versioning, and let a lifecycle rule
retain deleted versions for 90 days, so an attacker can delete only what is current.

That is wrong. B2's `deleteFiles` capability authorises
[`b2_delete_file_version`](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities),
which deletes a specific version [permanently — "as if you never uploaded that
version"](https://www.backblaze.com/docs/cloud-storage-file-versions). Lifecycle rules govern
*automatic* cleanup of hidden and superseded versions; they are not a barrier to explicit
deletion. An attacker with the machine's key can list every version and delete every one.

## Decision

What is established, and what this record commits to:

**Credential scoping alone cannot provide tamper resistance.** restic requires delete
permission, and on B2 delete permission means permanent version deletion. No arrangement of
capabilities on a key the machine holds can prevent the holder of that machine from destroying
the archive. The property has to be enforced by the storage service against a key that *can*
delete, or it does not exist.

**Nothing may claim otherwise until a mechanism is verified.** `pless audit` must not report a
setup as tamper-resistant on the strength of versioning and a lifecycle rule, and the
documentation says plainly that the off-site copy is destroyable by whoever holds the machine.

**The machine's key is still scoped to one bucket**, with no `writeBuckets`, `deleteBuckets`,
`writeBucketRetentions`, `writeKeys` or `deleteKeys`. This limits the blast radius to one
bucket and stops the key reconfiguring the bucket. It is worth doing and it is not tamper
resistance.

**The mechanism is not chosen.** See below; it needs verifying against a real B2 account and a
real restic run before it is written down as decided. That work is tracked in
[issue #14](https://github.com/kschulst/pless/issues/14), which this record is waiting on.

## Consequences

Until a mechanism is settled, the honest position is that an attacker who takes the machine can
destroy the off-site copy, and the answer to that is a second copy somewhere the machine has no
credentials for — `pless backup mirror`, to an external disk or a repository the target cannot
reach.

The audit work in stage 4 shrinks accordingly: it can check that the key is scoped to one bucket
and cannot change bucket settings, which is true and useful, but it cannot certify immutability.

This record is `Proposed` rather than `Accepted` on purpose. A register full of decisions that
were never really made is worse than a gap, and the gap is the accurate description of where
this stands.

## Alternatives still open

- **B2 Object Lock**, most likely in governance mode: locked versions cannot be deleted before
  their retention expires, and only a key with `bypassGovernance` can override that — which the
  machine's key would not have. This is the leading candidate. The open question is what it does
  to restic: the native `b2` backend deletes file versions, which Object Lock would refuse, so
  lock files could not be cleaned up. Pointing restic at B2's S3-compatible endpoint instead
  turns a delete into a delete marker, which Object Lock permits, at the cost of retaining data
  for the lock period. **Both halves need testing against a real account.**
- **An append-only write path**, such as a restic REST server in `--append-only` mode on a host
  the target cannot otherwise reach. Genuinely enforces the property, and adds a service to run.
- **Pull rather than push**: another machine fetches from the target. Removes cloud credentials
  from the target entirely, and reintroduces the problem ADR 0013 rejected — backups that happen
  only when something else is awake.
- **Accepting the exposure** and relying on `mirror` for the second copy. The status quo of this
  record, and the honest default until one of the above is verified.
