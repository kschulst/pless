# Getting documents back

**The question this page answers:** is the backup real, and what do you actually do when you
need it?

A backup nobody has restored from is a belief. `pless` is built around that sentence, so most
of what follows is about producing evidence rather than about taking snapshots.

## Is it working?

```bash
pless backup status
```

```console
Repository: s3
Timer: active (daily)
Snapshots: 1
Latest: 6ba5bd16 at 2026-10-03T22:57:57+02:00
✓ Last run 2026-10-03T20:57:58Z: 5 documents, snapshot 6ba5bd16.
✓ Verified 2026-10-03T20:58:24Z: A new machine was built from nothing and produced 5
  documents from snapshot 6ba5bd16.
```

Four things have to be true, and the output says each one separately: snapshots exist, the
timer is running, the last run succeeded, and a restore has been verified recently.

**"No snapshots tagged pless" is a failure, not "nothing yet."** A repository that was
silently recreated looks exactly like success and contains nothing. The command says so in
those words rather than reporting zero.

**A skipped run is not a failed one.** Two things skip calmly: a locked volume, which is the
normal state after a reboot, and a busy task queue, which is the normal state during an import.
`status` shows both as skips. A queue that stays busy for `[backup] max_busy_skips` runs becomes a
failure, because by then an import is no longer the likely explanation.

**A verification that has expired counts as none.** `[backup] verify_max_age_days` decides
how long one is worth; past that, `pless preflight` goes back to amber. That is intended — a
restore proven eleven months ago proves little about a repository written to every day since.

## Prove it right now

```bash
pless backup verify
```

This restores a sample of documents from the newest snapshot and compares what came back
against what the run that produced it recorded. restic verifies content hashes as it
restores, so a sample that comes back *is* a sample proven intact.

Cost scales with `[backup] verify_sample_size`, not with the size of the archive, which is
what makes it cheap enough to run weekly on a timer.

```bash
pless backup verify --level full
```

This is the one that proves the *procedure* rather than the data. It creates a second machine,
bootstraps it, gives it an encrypted volume and a Paperless stack, restores the snapshot into
it and counts what came back — then destroys it.

```console
  Creating arkiv-01-drill via lima. This takes a while…
  Creating an encrypted volume…
  Deploying Paperless…
  Restoring snapshot 6ba5bd16 and importing it…
  Destroying arkiv-01-drill…
✓ A new machine was built from nothing and produced 5 documents from snapshot 6ba5bd16.
```

It costs a VM, a few gigabytes of downloads and tens of minutes, so run it deliberately —
never on a timer. A rehearsal that *fails* leaves its VM standing, because that is the one
worth looking at.

It needs a repository the second machine can reach, so it refuses a local repository and tells
you to use `--level content` instead.

## The machine is gone

This is the case the whole design exists for. Nothing about it needs the old machine.

```bash
pless vm create          # or point [host] at new hardware
pless bootstrap
pless storage init --confirm
pless deploy paperless
pless backup restore --confirm
```

`restore` asks you to type the host label, then puts the export tree and the database dump
back where they came from and runs Paperless's importer over them.

**It refuses a target that already holds documents.** Paperless's importer merges into
whatever is there, and nothing in the result records which document came from which — so a
restore onto a live archive leaves an installation that is neither the backup nor what it was
before, with no way back. Restore into a new machine.

Then, and this matters:

```bash
pless backup init
```

The restored machine is not backing anything up until you do. A restored machine that never
takes a snapshot is one failure away from where you started.

You will need two secrets from your password manager: the `RESTIC_PASSWORD` that opens the
repository, and the object-storage credentials that reach it. Neither can be recovered from
anywhere else — see [Secrets](../reference/secrets.md).

## I just need one document

```bash
pless backup extract --confirm
```

This pulls the documents out of a snapshot in the clear, onto the machine you run it from.
Nothing plaintext is staged on the target: restic writes a tar to standard output, which
travels over SSH and is unpacked locally.

It refuses without `--confirm`, and the refusal names the directory it would write to, because
this is the one command that puts readable documents on an unencrypted disk. Delete what it
produces when you are done with it
([ADR 0018](https://github.com/kschulst/pless/blob/main/adr/0018-no-plaintext-copy-on-the-operators-machine.md)).

What you get is readable files, not a re-importable export — thumbnails and Paperless's
manifests are left out. `pless backup restore` is the command that puts an archive back.

## The bucket is filling up

```bash
pless backup forget
```

A dry run by default: it prints what the retention policy in `[backup]` would remove and
removes nothing. To apply it:

```bash
pless backup forget --prune --confirm
```

That also asks you to type the repository's name, the way `pless vm destroy` asks for the VM's.

**Pruning often reclaims nothing, and that is not a failure.** Under Object Lock a delete
becomes a delete marker and the data stays until the retention window expires, so an archive
under a 90-day lock carries roughly three months of churn. That is the price of immutability.
The command says so rather than leaving it looking broken.

## What the audit is telling you

`pless audit` checks the bucket as well as the network. Three findings are worth knowing by
sight.

**"The key was minted without readBucketRetentions."** The machine cannot read the Object Lock
configuration it depends on, so nothing about the bucket can be verified at all. A protection
you cannot verify is one you are trusting rather than checking. Fix:
`pless b2 provision --bucket <name> --new-key`.

**"Object Lock is enabled on the bucket but no default retention is set."** This is the one to
take seriously, because it looks like protection and is none — it is what a bucket looks like
when the lock was switched on and nothing else was. Nothing written to it is retained. Fix:
re-run `pless b2 provision`, which repairs the retention.

**"The key on this machine holds bypassGovernance."** That capability defeats Object Lock
entirely, so a compromised machine could destroy every version in the bucket. A key created in
Backblaze's web console carries it, along with 28 other capabilities, regardless of what you
selected. Fix: `pless b2 provision --bucket <name> --new-key`, which mints one that cannot.

## What backup does not protect you from

**A document you deleted on purpose, a while ago.** Retention is finite. Past
`retention_daily` and its siblings, a deleted document is gone from the snapshots too.

**Losing `RESTIC_PASSWORD`.** The repository becomes an encrypted blob nobody can open,
including you. There is no recovery path and `pless` cannot build one.

**Losing the Backblaze account itself.** Object Lock protects against a compromised machine,
not against an account suspension — and `pless backup mirror <repository>` is the answer to
that. For a genuinely different provider the destination is an rclone remote, because restic
cannot hold two sets of credentials for one backend; see
[Commands](../reference/commands.md) for which destinations work.

**A corrupted source.** Backup copies what is there. If Paperless damaged a document a week
ago and you notice today, the snapshots have the damaged version — which is what makes
`verify` worth running weekly rather than yearly.
