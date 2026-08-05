# SPDD Analysis: Backup and restore drills

**Issue:** [#2 — Back up to off-site object storage with restic](https://github.com/kschulst/pless/issues/2)
**Related:** [#3 — Verify a backup by actually restoring it](https://github.com/kschulst/pless/issues/3)

## Original Business Requirement

### Issue body

> **Decided approach:** [ADR 0013](../../adr/0013-backup-offsite-and-drilled.md) — two
> layers, and the off-site one goes to a *different provider* from wherever the machine runs.
>
> **Scope**
> - `pless backup export` — run Paperless's document exporter on the target
> - `pless backup download` — pull the export to `./backups`
> - restic snapshots to S3-compatible storage (Backblaze B2 by default)
> - Scheduled with a systemd timer
> - Keep originals and metadata separable, so extracting "just the documents" stays trivial
> - Backups must support encryption
> - We should explore alternative locations for backups such as Dropbox
>
> `RESTIC_PASSWORD` becomes a second secret with no recovery path. Setup must say so loudly.
>
> Blocked on nothing. Should land close behind import — until it exists, `pless preflight`
> correctly refuses to give a green light.

### Requirements (settled in a grilling session, 2026-08-05)

> #### What gets backed up
>
> Both, because neither alone is sufficient:
>
> 1. **`document_exporter`** — a portable, version-independent export that can be imported
>    into a fresh Paperless with `document_importer`. This is what survives a Paperless
>    major-version change, and its layout keeps original files and metadata separable, so
>    extracting *just the documents* stays trivial.
> 2. **A SQL dump** (`pg_dump`) — a point-in-time consistent database.
>
> Copying the Postgres data directory while the container runs produces a corrupt backup, so
> raw file copy of `postgres/` is explicitly **not** a strategy.
>
> The exporter requires that nothing is being consumed while it runs. The implementation must
> ensure quiescence rather than hoping for it, and must fail loudly if it cannot.
>
> #### Where it goes
>
> `restic`, encrypted, to an S3-compatible repository. Two repository kinds must be supported:
>
> - **Remote** (Backblaze B2 by default) — the real off-site layer, deliberately at a
>   different provider from wherever the machine runs.
> - **Local directory** — a path on the target or an attached disk.
>
> The local kind is not a lesser mode. It makes the entire backup and restore flow testable
> without any cloud account, which means the restore drill can be exercised in CI and in a
> throwaway VM.
>
> #### The machine must not be able to delete its own backups
>
> This follows directly from the threat model in
> [ADR 0005](../../adr/0005-the-local-network-is-hostile.md): if the machine can be
> compromised or stolen, then whoever has it also has the credentials stored on it. A single
> full-access key means an attacker deletes the archive and its rescue in one operation.
>
> - The machine gets a B2 application key with **`listBuckets`, `listFiles`, `readFiles`,
>   `writeFiles` and no delete capability**.
> - Retention (`restic forget --prune`) runs **from the operator's machine** with a separate,
>   privileged key. It is an occasional, deliberate act, not something the target does on a
>   timer.
> - `pless doctor` or `pless audit` should be able to tell the operator which kind of key the
>   target holds, because a full-access key on the target silently removes the protection.
>
> #### How it runs
>
> On the target, driven by a systemd timer, pushing to the repository. The operator's laptop
> is asleep, travelling or reinstalled too often to be part of a backup schedule — an
> always-on machine is the point of the exercise.
>
> #### Verifying a restore
>
> `pless backup verify` must prove the documents come back, not that an archive file exists.
> Two levels, both required:
>
> 1. **Content verification** (fast, suitable for frequent or automated runs) — restore the
>    most recent snapshot to a scratch location, compare document count against the source,
>    and compare hashes for a random sample of files. Cost scales with the sample, not the
>    archive.
> 2. **Full restore rehearsal** (slow, occasional) — restore the whole archive into a
>    throwaway VM, start Paperless, and confirm it comes up with the documents present. This
>    is the only level that also tests whether the *procedure* works, not just the data.
>
> `pless preflight` currently hard-codes `backup_verified=False`. Wiring it to a real
> verification result is part of this work.
>
> #### Secrets
>
> - `RESTIC_PASSWORD` — a second secret with no recovery path. Lose it and the off-site backup
>   is an encrypted blob nobody can open, including its owner. Setup must say so as loudly as
>   it does for the LUKS passphrase.
> - `B2_ACCOUNT_ID` / `B2_ACCOUNT_KEY` — the restricted key on the target.
> - The privileged pruning key lives only on the operator's machine and never reaches the
>   target.
>
> All of these follow [ADR 0014](../../adr/0014-secrets-never-in-argv.md): stdin, never argv.
>
> #### Out of scope
>
> - Restoring *into* an existing installation with data already in it. Restore targets a fresh
>   install.
> - Backing up the operating system. The machine is reproducible from `pless bootstrap`; only
>   the archive is irreplaceable.
>
> #### Open questions for analysis
>
> - Retention policy defaults — how many daily, weekly, monthly snapshots.
> - Whether the exporter should run with `--delete` to keep the export directory from growing,
>   and what that means for a partially failed run.
> - Whether the local-directory repository should also be offered on the operator's machine,
>   pulled over SSH.

## Domain Concept Identification

### Existing Concepts (from codebase)

- **Host** (`targets.Host`): an SSH-reachable machine, described by `[host]`. Everything the
  backup does on the target flows through it. Backup adds no new notion of "where".
- **Encrypted volume** (`storage`): LUKS2 volume mounted at `/opt/paperless`, unlocked
  manually after boot. Backup depends on it entirely — a locked volume means there is nothing
  to read, which makes "is it unlocked?" a precondition rather than an error.
- **Paperless stack** (`deploy`, `composegen`): five compose services under a
  `paperless.service` unit gated on the mount. Both the exporter and `pg_dump` must run
  *inside* this stack, so the stack has to be up while backup runs.
- **Install directory** (`composegen.INSTALL_DIR` = `/opt/paperless`) with subdirectories
  already provisioned by `deploy`, including `export/` and `backups/`. Both live on the
  encrypted volume, so anything staged there is encrypted at rest for free.
- **Secrets** (`config.Secrets`): `restic_password`, `b2_account_id`, `b2_account_key` are
  already declared and currently unused — placeholders waiting for this work.
- **Backup configuration** (`config.BackupConfig`): a single `restic_repository` string,
  declared and unused.
- **Readiness gate** (`preflight`): already models `backup_configured` and `backup_verified`
  as blocking checks, both hard-coded false. This is the seam backup plugs into.
- **Host spec** (`hostspec`): the package list and the pattern for delivering configuration
  and units to a machine idempotently. restic is not in it yet.
- **Exposure audit** (`audit`): pure analysis over collected text, with findings that fail a
  build. The natural home for "does the target hold a key that can delete backups?".
- **Secret transport** (`sshexec`): secrets travel on stdin, never argv
  ([ADR 0014](../../adr/0014-secrets-never-in-argv.md)). Every credential this feature
  introduces inherits that rule.

### New Concepts Required

- **Backup repository**: where snapshots live. Has a *kind* — local directory, S3-compatible
  object storage, or an rclone-mediated remote such as Dropbox — but the tool should treat it
  as one concept with a location string, because restic already does.
- **Backup run**: one logical unit comprising quiescence, export, database dump, snapshot, and
  release. Its atomicity is the interesting property: a run that fails halfway must not look
  like a run that succeeded.
- **Quiescence**: the state in which Paperless is not consuming, so the exporter sees a stable
  set of documents. A precondition that must be established and released, not hoped for.
- **Snapshot**: one restic snapshot, identified and dated. Relates to a backup run one-to-one.
- **Retention policy**: how many snapshots of what age survive. Belongs to the operator's
  machine, not the target — a direct consequence of the no-delete rule.
- **Credential capability**: whether a given credential can delete what it has written. This
  is a *security property of a key*, not a configuration preference, and the whole
  anti-tamper design rests on it.
- **Verification record**: evidence that a restore actually succeeded, and when. Needed
  because `preflight` must answer "has this been verified?" across process boundaries, which
  means the answer has to be persisted somewhere rather than recomputed.
- **Verification level**: content comparison versus full restore rehearsal. Two different
  costs, cadences and guarantees; the same command with different depth.

### Key Business Rules

- **The machine cannot delete what it wrote.** Governs credential capability, retention
  location, and where pruning runs. This is the rule that makes backup meaningful under
  [ADR 0005](../../adr/0005-the-local-network-is-hostile.md).
- **A backup is not a backup until it has been restored.** Governs verification record and
  `preflight` — silence must never read as success
  ([ADR 0013](../../adr/0013-backup-offsite-and-drilled.md)).
- **Originals and metadata stay separable.** Governs the export layout, so extracting only the
  documents never requires understanding Paperless internals.
- **Backup runs only against an unlocked volume.** Governs preconditions; a locked volume is a
  normal state, not a failure.
- **Never delete anything the operator did not ask to delete.** Governs the exporter's
  `--delete` behaviour and any cleanup of staged artefacts.
- **A partially failed run must be visible as failed.** Governs run bookkeeping — the worst
  outcome is a snapshot that looks complete and is not.

## Strategic Approach

### Solution Direction

Backup is a **pipeline executed on the target**, invoked either by the operator or by a
systemd timer, following the same delivery pattern `deploy` already uses for
`paperless.service`: render a unit and its configuration, write them over SSH with secrets on
stdin, enable, and let systemd own the schedule.

The pipeline: establish quiescence → run `document_exporter` into `export/` → `pg_dump` into
the same staging area → `restic backup` that directory to the configured repository → release
quiescence. Staging on the encrypted volume means intermediate artefacts are protected at rest
without extra work.

Repository kind is deliberately *not* modelled as a branching concern in `pless`. restic
already speaks local paths, S3/B2, and — through rclone — Dropbox and much else. Treating the
repository as an opaque location string keeps `pless` out of the storage-backend business and
makes "explore alternative locations such as Dropbox" a documentation question rather than a
code question.

Verification is the same command at two depths, reusing the restore path in both cases so the
cheap level exercises the same code as the expensive one.

The analysis modules follow the established shape: pure functions over collected text, with
I/O at the edges, so both verification levels and the credential-capability check are testable
without a machine ([ADR 0009](../../adr/0009-cli-is-a-library-with-a-terminal-frontend.md)).

### Key Design Decisions

- **Repository as an opaque location string, not a typed backend**: trades a small loss of
  validation for the entire rclone ecosystem arriving free, and avoids `pless` growing a
  provider abstraction that duplicates restic's. → **Recommended.** It also mirrors the
  reasoning in [ADR 0016](../../adr/0016-there-is-only-a-host.md), where refusing to enumerate
  kinds turned out to widen support rather than narrow it.

- **Quiescence by pausing consumption, not by stopping the stack**: `pg_dump` needs the
  database up and the exporter needs the application up, so stopping the stack is not
  available even if downtime were acceptable. → **Recommended**, with the caveat that the
  precise mechanism must be verified against Paperless (see Risks) — this is the decision most
  likely to need adjustment.

- **Retention runs from the operator's machine, never on a timer**: the direct cost of the
  no-delete rule. Trades unattended tidiness for the property that a compromised machine
  cannot destroy history. → **Recommended**, and the trade is the point rather than a
  regrettable side effect.

- **Verification record persisted on the target**: `preflight` runs in a separate process from
  `verify`, so the answer to "has a restore succeeded?" must outlive the process that
  established it. Storing it beside the data keeps it consistent with what it describes.
  → **Recommended**, with the caveat that a record on the target is only as trustworthy as the
  target, which matters if the machine is compromised.

- **Credential capability treated as an audit finding, not a config flag**: whether the key can
  delete is a fact about the world, discoverable by asking the provider, and the operator can
  get it wrong silently. Putting it in `audit` makes drift a failing check rather than a
  belief. → **Recommended.**

- **Local-directory repository is a first-class kind, not a test fixture**: it is what allows
  the restore drill to run in CI and in a throwaway VM, which is what stops "verify" from
  being aspirational. → **Recommended**, provided the documentation is blunt that a local
  repository on the same disk protects against deletion and corruption, not against loss of
  the machine.

### Alternatives Considered

- **`document_exporter` alone**: simpler, and sufficient to rebuild a working Paperless.
  Rejected because it loses point-in-time database consistency and forces a full re-import for
  any restore.
- **`pg_dump` plus raw file copy, no exporter**: fastest and smallest. Rejected because it
  couples the backup to one Paperless version and one deployment shape, and the portable copy
  is precisely what protects against a bad upgrade.
- **restic on the operator's machine pulling over SSH**: keeps all cloud credentials off the
  target, which is genuinely attractive under our threat model. Rejected because backups would
  then happen only when a laptop is awake and someone remembers — and "remembers" is not a
  backup strategy.
- **B2 Object Lock instead of a restricted key**: a stronger immutability guarantee. Rejected
  as the default because a lock cannot be lifted before it expires, so a misconfigured
  retention binds the operator to paying for data they no longer want. Worth documenting as an
  option for those who want it.
- **A typed provider abstraction (`b2` / `s3` / `local` / `dropbox`)**: better validation and
  error messages. Rejected as duplicating restic's own backend handling, and as the kind of
  enumeration that tells users with an unlisted provider that the tool is not for them.

## Risk & Gap Analysis

### Requirement Ambiguities

- **What "ensure quiescence" means concretely**: the requirement states it must be ensured and
  must fail loudly, but Paperless's actual mechanism for pausing consumption is not
  established. This must be resolved before design, because it determines whether a backup run
  can be non-disruptive at all.
- **Where the verification record lives, and whose word it is**: `preflight` needs a durable
  answer, but a record stored on the target is trusted exactly as much as the target. Whether
  that is acceptable, or whether the operator's machine should hold it, is unsettled.
- **What "the archive" means for the content-verification comparison**: comparing document
  count "against the source" is unambiguous while the stack is up, but the natural time to
  verify is right after a backup, when the source may have moved on. The comparison baseline
  needs pinning down.
- **How far `--delete` on the exporter should go**: listed as an open question, and it
  interacts with the rule about never deleting what the operator did not ask to delete.

### Edge Cases

- **The volume is locked when the timer fires.** After every reboot this is the normal state,
  and it will happen regularly given automatic reboots. A locked volume must produce a clear,
  non-alarming outcome — not a failure that trains the operator to ignore backup alerts.
- **A backup run overlaps the next scheduled run.** A large first snapshot over a slow
  connection can outlast the interval.
- **The export directory has grown to occupy the volume.** Staging doubles the archive on the
  same encrypted volume, which interacts directly with `data_size_gb` sizing guidance already
  published in the configuration reference.
- **Restore into a machine whose Paperless version differs from the one that produced the
  export.** The exporter exists precisely for this, but it is the case least likely to be
  exercised before it is needed.
- **The repository is empty** — first run, or a repository that was silently recreated. The
  second is the dangerous one: it looks like success and contains nothing.
- **`RESTIC_PASSWORD` is wrong or missing** at restore time, which is the moment when the
  consequences are highest and the operator is least calm.
- **The staged export and the SQL dump are taken seconds apart**, so a document consumed
  between them appears in one and not the other. Whether that matters depends on the restore
  procedure's order of operations.

### Technical Risks

- **A no-delete credential may break restic's own locking.** restic creates and removes lock
  files during normal operation. With a credential that cannot delete, stale locks may
  accumulate and eventually block backups — the failure would appear weeks later, as backups
  silently stopping. **This is the highest-impact unknown in the design and must be verified
  experimentally against a real B2 restricted key before the canvas commits to it.** Mitigation
  directions if confirmed: lock cleanup as part of the operator-side retention run, or a
  rest-server-style append-only proxy instead of a restricted key.
- **restic is not in the host spec.** Adding it is small, but it is a new package and a new
  version surface on both supported distributions.
- **Dropbox as a repository requires rclone and an OAuth flow**, which is awkward on a headless
  machine — the token must be obtained interactively elsewhere and transported. This makes
  Dropbox meaningfully harder than B2 rather than an equivalent option, and the documentation
  should say so rather than listing them as peers.
- **Backup competes with OCR for a constrained machine.** On a Raspberry Pi, a snapshot during
  ingestion may make both slower in ways that look like a hang.
- **A local repository on the same disk protects against very little.** It is the right tool
  for testing and for accidental deletion, and no protection at all against the loss of the
  machine — the scenario backup exists for. Presenting the two kinds as equals in
  documentation would be actively misleading.
- **The verification record is a claim the target makes about itself.** If the machine is
  compromised, so is the record, and `preflight` would report a reassuring falsehood.

### Acceptance Criteria Coverage

| AC# | Description | Addressable? | Gaps/Notes |
|-----|-------------|--------------|------------|
| 1 | `pless backup export` — run the document exporter on the target | Yes | Depends on resolving quiescence; the exporter runs inside the stack via the existing compose helper |
| 2 | `pless backup download` — pull the export to `./backups` | Yes | `[paths] local_backups` already exists and is unused |
| 3 | restic snapshots to S3-compatible storage, B2 by default | Yes | Requires restic in the host spec |
| 4 | Scheduled with a systemd timer | Yes | Follows the existing unit-delivery pattern; must handle the locked-volume case gracefully |
| 5 | Originals and metadata separable | Yes | A property of the exporter's layout; needs a test asserting it rather than trusting it |
| 6 | Backups must support encryption | Yes | restic is encrypted by construction; `RESTIC_PASSWORD` becomes a second unrecoverable secret |
| 7 | Explore alternative locations such as Dropbox | Partial | Reachable through rclone, but the OAuth flow on a headless machine is materially harder than B2 — belongs in documentation with that caveat stated, not as an equal option |
| 8 | Local-directory repository as a first-class kind | Yes | The enabler for CI and throwaway-VM drills |
| 9 | Machine holds a credential that cannot delete | **At risk** | Depends entirely on the restic locking question above |
| 10 | Retention runs from the operator's machine | Yes | Follows from AC 9; needs a separate privileged credential that never reaches the target |
| 11 | `pless backup verify` — content comparison | Yes | Comparison baseline needs pinning down |
| 12 | `pless backup verify` — full restore rehearsal into a throwaway VM | Yes | Couples backup to the `vm` module; only meaningful with a local or reachable repository |
| 13 | `preflight` wired to a real verification result | Yes | Requires the verification-record concept; trust boundary noted above |
| 14 | Audit can report which kind of credential the target holds | Yes | Fits the existing audit shape; discovery mechanism needs establishing |
