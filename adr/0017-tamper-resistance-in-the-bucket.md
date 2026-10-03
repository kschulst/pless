# 0017 — Tamper resistance comes from Object Lock, not from withholding delete

- **Status:** Accepted
- **Date:** 2026-08-08
- **Status note:** amended by [0020](0020-the-machine-key-can-read-the-lock.md), which adds
  `readBucketRetentions` to the capability list below — the `pless audit` check promised here
  cannot be performed by a key that holds only the five
- **Context references:** issues #2 and #14; amends [0013](0013-backup-offsite-and-drilled.md)

## Context

[ADR 0005](0005-the-local-network-is-hostile.md) assumes the machine can be compromised or
stolen. Whoever holds it holds every credential on it, so a backup credential with full access
means an attacker destroys the archive and its rescue in one operation.

Two candidate answers were tried, and both failed.

**A key that cannot delete.** restic creates and removes files in `locks/` during an ordinary
backup and [cannot work without that right](https://github.com/restic/restic/issues/3491). The
`--no-lock` workaround removes protection against concurrent operations, and this design runs a
backup timer and a verification timer against the same repository.

**Versioning plus a lifecycle rule.** This record originally claimed it: give the machine
`deleteFiles`, enable versioning, and let a lifecycle rule keep deleted versions. That is wrong.
B2's `deleteFiles` authorises
[`b2_delete_file_version`](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities),
which removes a version [permanently](https://www.backblaze.com/docs/cloud-storage-file-versions).
Lifecycle rules govern *automatic* cleanup of hidden and superseded versions; they are no barrier
to explicit deletion.

So the property cannot come from the credential. It has to be enforced by the storage service
against a key that *can* delete.

## Decision

**Object Lock, in governance mode, with a default retention period on the bucket.** The machine's
key writes and deletes as restic requires, and the service refuses to destroy locked versions.

The machine's key is created with `b2_create_key`, restricted to one bucket, holding exactly
`listBuckets`, `listFiles`, `readFiles`, `writeFiles` and `deleteFiles` — and **not**
`bypassGovernance`. That single exclusion is the entire protection.

The repository is addressed through B2's **S3-compatible endpoint**
(`s3:https://s3.<region>.backblazeb2.com/<bucket>`) rather than the native `b2:` backend, because
a delete through the S3 API becomes a delete marker, which Object Lock permits.

`pless audit` verifies that the key lacks `bypassGovernance` and that the bucket carries a default
retention period. It does not accept versioning or a lifecycle rule as evidence of anything.

## Consequences

This was verified against a real Backblaze bucket rather than inferred (issue #14). With a scoped
key, `init`, `backup`, lock cleanup, `check`, `forget --prune` and `restore` all work normally, a
determined deletion attempt returns **Access Denied**, and the archive restores byte-identical
afterwards. With a key holding `bypassGovernance`, the same attack leaves **zero versions** and no
repository.

**The B2 web console cannot produce a safe key.** A console key with "Read and Write" comes back
holding all 29 capabilities — including `bypassGovernance`, `deleteBuckets` and `writeKeys` — and
ignores the bucket restriction. An operator following the obvious path gets a key that defeats
Object Lock entirely. The safe key exists only through the API.

**The console cannot produce a correct bucket either.** Its create-bucket dialog offers Object
Lock as "Compliance mode only", while `b2_update_bucket` sets governance without complaint.
Compliance is the wrong default here: nothing can be deleted before expiry by anyone, and
Backblaze's remedy for a lock period set too long is closing the account.

Both of those make the setup API-first, and together they are the argument for `pless` provisioning
the bucket and the key itself rather than documenting a sequence of clicks that cannot produce the
right result.

**Object Lock prevents destruction, not disruption.** A scoped key that cannot delete versions can
still create delete markers and leave stale locks. During the drill the repository stopped
responding until `restic unlock --remove-all` cleared four locks. No data was at risk; availability
briefly was, and the documentation says so and names the command.

**Storage grows rather than shrinks.** Deletes become delete markers and `prune` reclaims nothing
until retention expires, so an archive under a 30-day lock carries roughly a month of churn. That
is the price of immutability, and it is small at these volumes.

Retention no longer needs to run from the operator's machine: `pless backup forget --confirm` runs
on the target, because a compromised machine that prunes cannot destroy what the lock protects. It
stays a deliberate act, never a timer.

## Alternatives considered

- **A key without delete capability:** cannot drive restic at all.
- **`restic --no-lock`:** would make that work, at the cost of concurrency protection, with two
  timers against one repository.
- **Compliance mode:** a stronger guarantee and the wrong trade. It binds the operator as much as
  the attacker, and a misconfigured period is unfixable short of closing the account.
- **The native `b2:` backend:** untested under Object Lock and expected to fail, since it deletes
  file versions rather than creating delete markers. The S3 endpoint is verified, so that is what
  the documentation specifies.
- **An append-only restic REST server:** genuinely enforces the property, and adds a service to run
  and keep alive.
- **A second cloud provider:** doubles configuration and failure modes for a scenario
  `pless backup mirror` already covers.
