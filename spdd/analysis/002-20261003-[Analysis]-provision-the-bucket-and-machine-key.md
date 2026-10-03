# SPDD Analysis: Provision the B2 bucket and machine key from pless

- **Issue:** [#16](https://github.com/kschulst/pless/issues/16)
- **Date:** 2026-10-03
- **Decisions already recorded:** [ADR 0020](../../adr/0020-the-machine-key-can-read-the-lock.md),
  [ADR 0021](../../adr/0021-provision-the-bucket-with-a-credential-pless-never-stores.md)

Every open question in this requirement was settled in dialogue on the issue and **measured
against a real Backblaze account** before this analysis was written. Per
[`spdd/README.md`](../README.md), an analysis whose open questions are still open is not ready
to become a canvas; the questions remaining at the end are design choices for the canvas, not
unknowns about the world.

## Original Business Requirement

Reproduced verbatim from issue #16 and its comments, headings and all. Its `../blob/main/...`
links are GitHub-relative and resolve on the issue rather than from this file; the analysis
below links to the same records by repository path.

# Issue #16 — Provision the B2 bucket and machine key from pless, not from the console

## Description (original)

The correct off-site setup **cannot be produced from Backblaze's web console**, in two independent ways. Both were confirmed against a real account in #14.

- A console key with "Read and Write" comes back holding **all 29 capabilities**, including `bypassGovernance` — the one capability that defeats Object Lock — and ignores the bucket restriction you selected. Tested: that key wipes an Object Lock bucket to zero versions.
- The create-bucket dialog offers Object Lock as **"Compliance mode only"**. Governance is what we want, and it is reachable only through `b2_update_bucket`. Compliance binds the operator as much as the attacker, and Backblaze's stated remedy for a period set too long is closing the account.

So the documentation would have to read: *"open a terminal, install the B2 CLI, and make these three API calls"* — for the step that decides whether your backups can be destroyed. That is a setup people will get wrong, and wrong here is invisible until the day it matters.

## Proposal

`pless backup provision` — an **operator-side** command, run from your machine, that:

1. creates the bucket with Object Lock enabled (it cannot be added later)
2. sets the default retention to governance for `[backup] version_retention_days`
3. mints a machine key restricted to that bucket with exactly `listBuckets`, `listFiles`, `readFiles`, `writeFiles`, `deleteFiles` — and no `bypassGovernance`
4. prints the key once, for the password manager, and writes the repository URL for `pless.toml`

`pless audit` then verifies the result, which is a check we already want independently.

## Why this does not violate the opaque-repository rule

The canvas is firm that the repository is an **opaque location string** and that `pless` grows no provider abstraction. That still holds: nothing in the backup path learns about B2.

This is the [ADR 0016](../blob/main/adr/0016-there-is-only-a-host.md) pattern applied to storage. `pless hetzner` is a way to *obtain* a machine while `[host]` stays generic and provider-free; `pless backup provision` is a way to *obtain* a bucket while `restic_repository` stays a string. Optional, provider-specific, and entirely outside the path that does the work.

It is also what makes a future web wizard possible, since click-ops cannot be wizarded — and in this case the clicks produce the wrong answer anyway.

## Open questions for grilling

- Does this belong in `pless` at all, or in a documented script? The counter-argument is that every provider added is a maintenance surface, and B2 is currently the only one.
- Which credential does it use? It needs `writeBuckets`, `writeBucketRetentions` and `writeKeys` — powerful, operator-side, and never on the target. A separate `[backup] provisioning key` in `.env`, deleted afterwards?
- Should it be idempotent against an existing bucket, or refuse and tell you to use `audit`?
- What happens when the bucket exists but was made in the console with compliance mode — repair, or refuse?

A working prototype already exists as the throwaway script from #14, which did exactly these four steps against a real account.

Depends on nothing; stage 4 of #2 (the audit checks) is the natural companion.

## Comments (the grilling)

author:	kschulst
association:	owner
edited:	false
status:	none
--
## Grilling outcome

Four decisions settled, one measurement that invalidates part of the canvas, and one question still open. This supersedes the **Open questions for grilling** section in the description above.

### 1. No `--from-master`. The master key never reaches pless.

The first instinct was a `--from-master` flag that mints the provisioning key for you. Dropped, because the risk is not where the convention guards it:

- **Blast radius is the whole account.** B2's master application key cannot be restricted, there is one per account, and it holds `bypassGovernance` — the capability [ADR 0017](../blob/main/adr/0017-tamper-resistance-in-the-bucket.md) exists to withhold. It cannot be deleted, only regenerated.
- **The likely leak path is output, not argv.** `b2_authorize_account` sends the key as HTTP Basic auth and answers with a bearer token carrying the master key's full capabilities for 24 hours. This project settles arguments by pasting real API output into issues (#14 is the precedent). An error message or verbose dump that includes the `Authorization` header or that token, pasted by someone debugging, is full account compromise. [ADR 0014](../blob/main/adr/0014-secrets-never-in-argv.md)'s stdin rule does not cover it.
- **A flag invites reuse.** The master key is needed once per account; a flag on the command you run most often keeps bringing it back.

**Instead:** `pless b2 provision` reads the provisioning key on stdin and **refuses a credential that itself holds `bypassGovernance`**. The refusal message prints the single `b2_create_key` call that mints a correct one.

That refusal is what makes the whole feature worth building. Without it, `provision` would merely *promise* to omit `bypassGovernance` — which is exactly what ADR 0017 ruled insufficient. With it, a B2 key cannot grant capabilities it does not itself hold, so a dangerous machine key is not unlikely but **unobtainable**. Same move ADR 0017 made: remove the possibility rather than promise it will not happen.

Note what the alternative actually costs: one documented API call, whose correctness is then *checked* by the next command. That is unlike the bucket, where a wrong answer from the console is invisible — which is why the bucket needs provisioning and this does not.

**Requirement that follows:** the B2 client must never render request headers or the authorization token. Errors are reconstructed from status code and B2's own error body.

### 2. The command is `pless b2 provision`, not `pless backup provision`.

| | `pless backup …` | `pless b2 …` |
|---|---|---|
| Talks to | the target over SSH | `api.backblazeb2.com` from the operator's machine |
| Needs | `[host]` resolved, volume mounted | a provisioning credential; no host |
| Knows the provider | **never** | **only that** |

All six existing `pless backup` subcommands open with `cfg = load_config()` then `target = _host(cfg)`. `provision` would be the only one that resolves no host, and the first to know what B2 is — inside the namespace the canvas protects from provider knowledge.

The symmetry this follows:

```
pless vm create     → a way to obtain a HOST    → [host] stays generic
pless hetzner …     → a way to obtain a HOST    → [host] stays generic
pless b2 provision  → a way to obtain a BUCKET  → restic_repository stays a string
```

Correction to the description above: this is the [ADR 0016](../blob/main/adr/0016-there-is-only-a-host.md) pattern, but `pless backup provision` is the name that breaks it.

**Also a correction:** the description cites `pless hetzner` as precedent for "a way to obtain a machine". That precedent does not exist. `hetzner.py` is 42 lines and only verifies a token with read-only calls; nothing in pless creates a server (#5 is still open). `pless b2 provision` would be the *first* command to create resources at a provider — not a reason against it, but the argument cannot lean on a pattern that has never been built.

### 3. The ADR argues that the credential is never persisted, not that the laptop is safe.

"The laptop is not always on and not exposed" is true and too weak to carry the design — a laptop is stolen, gets malware, and syncs `.env` to a cloud backup. The stronger claim is mechanical: the provisioning key is read on stdin, used for four API calls, and never written to `.env`, `pless.toml` or argv. Same shape as ADR 0014, which solved this by moving secrets to stdin rather than arguing that argv is acceptable.

### 4. Measured: the machine key cannot see whether Object Lock is on.

Probed against a real bucket with a correctly restricted machine key — `listBuckets, listFiles, readFiles, writeFiles, deleteFiles`, no `bypassGovernance`. Read-only calls only (`b2_authorize_account`, `b2_list_buckets`):

```
== what the machine key can see about ITSELF ==
  capabilities: ['deleteFiles', 'listBuckets', 'listFiles', 'readFiles', 'writeFiles']
  bypassGovernance present: False

== what the machine key can see about the BUCKET ==
  fileLockConfiguration: {
      "isClientAuthorizedToRead": false,
      "value": null
  }
```

B2 returns the field and **redacts the value** for a key not authorised to read it.

So stage 4's two audit checks split:

| Check | Can it run on the target? |
|---|---|
| `check_backup_credential` — does the key lack `bypassGovernance`? | **Yes.** `allowed.capabilities` is visible. |
| `check_bucket_protection` — does the bucket carry default retention? | **No.** The value is redacted. |

**This invalidates the audit section of the canvas**, which specifies collecting both from the target by sourcing `backup.env` and calling `b2_list_buckets` with `curl`. That section was written before ADR 0017 landed. As specified it cannot distinguish "no Object Lock" from "not allowed to look", and either reading is harmful:

- treat `null` as no retention → CRITICAL on a correctly configured bucket, every time;
- treat a missing value as acceptable → **certify a destroyable setup by silence**, which is precisely what ADR 0017 refused when it rejected versioning as evidence.

### Still open

**Does `readBucketRetentions` on the machine key make `isClientAuthorizedToRead` true?** Reading a retention policy is read-only and grants no power, so adding it costs nothing defensively.

- **If yes:** the bucket check stays on the target, in `pless audit`, where the operator sees it — and ADR 0017's capability list is one short. Not because restic needs it, but because *a protection you cannot verify is one you are trusting rather than checking*. Same argument [ADR 0019](../blob/main/adr/0019-the-verification-record-lives-on-the-target.md) won for the verification record. ADR 0017 would need an amendment.
- **If no:** the bucket check moves operator-side as `pless b2 check`, and the `b2` group has two commands.

One API call decides it. Worth measuring rather than inferring, as #14 established.

### Revised scope

`pless b2 provision` — operator-side, provisioning key on stdin, never persisted:

1. refuse a credential holding `bypassGovernance`, printing the `b2_create_key` call that mints a correct one
2. create the bucket with Object Lock enabled (it cannot be added later)
3. set the default retention to governance for `[backup] version_retention_days`
4. mint a machine key restricted to that bucket with exactly the ADR 0017 capabilities
5. print the machine key once, and the `restic_repository` value for `pless.toml`

Still undecided and carried over from the description: idempotency against an existing bucket, what to do with a bucket the console made in compliance mode, where `<region>` in `s3:https://s3.<region>.backblazeb2.com/<bucket>` comes from, and whether `provision` writes `pless.toml`/`.env` the way `vm create` does or only prints (which interacts with #7).

**Needs an ADR before code:** the operator-side provisioning credential — why a credential this powerful is acceptable on the operator's machine, resting on it never being persisted rather than on the laptop being trusted.
--
author:	kschulst
association:	owner
edited:	false
status:	none
--
## Measured: `readBucketRetentions` opens the bucket check

The question left open above — whether adding `readBucketRetentions` to the machine key makes `fileLockConfiguration` readable — is answered. Minted a bucket-restricted key with ADR 0017's five capabilities plus that one, and probed read-only:

```
== what this key can see about ITSELF ==
  capabilities: ['deleteFiles', 'listBuckets', 'listFiles', 'readBucketRetentions',
                 'readFiles', 'writeFiles']
  bypassGovernance present: False
  readBucketRetentions present: True

== what this key can see about the BUCKET ==
  isClientAuthorizedToRead: True
  value: {
      "defaultRetention": { "mode": null, "period": null },
      "isFileLockEnabled": false
  }
```

Against the same bucket, the five-capability key returns `isClientAuthorizedToRead: false` and `value: null`.

### What follows

**The bucket check stays on the target, in `pless audit`.** No new command and no second credential: the operator sees it where they already look. So the `b2` group has one command, `provision`, and `pless b2 check` is not needed.

**ADR 0017's capability list is one short.** It needs `readBucketRetentions` — not because restic needs it, but because *a protection you cannot verify is one you are trusting rather than checking*, which is the argument [ADR 0019](../blob/main/adr/0019-the-verification-record-lives-on-the-target.md) already won for the verification record. The capability is read-only and grants no power: it cannot enable, disable or shorten anything. ADR 0017 needs an amendment adding it, with that reason.

**The three states become distinguishable**, which is what makes the check honest rather than a coin flip between two wrong answers:

| What the target sees | What it means |
|---|---|
| `isClientAuthorizedToRead: false` | the machine key was minted without `readBucketRetentions` — a fault in the key, and one `pless b2 provision` prevents |
| `isFileLockEnabled: false` | no Object Lock. CRITICAL, and genuinely so |
| `isFileLockEnabled: true` + a period | compare against `version_retention_days` |

Note that the first row stops being ambiguity and becomes a finding in its own right. The canvas's audit section can be rewritten against this table.

### Still unverified: what a real retention period looks like

The probed bucket has **`isFileLockEnabled: false`** — Object Lock is not on `pless-tesst`, so this run proved the field is *readable* but never showed a populated `defaultRetention`. `mode` and `period` were both `null`.

`check_bucket_protection` has to compare that period against `version_retention_days`, and the shape is not known from this: whether `period` is a number of days, a seconds count, or an object like `{"duration": 30, "unit": "days"}`. Guessing it would put a unit conversion in a check whose whole job is to be trustworthy.

So one more measurement is needed before that check is written, against a bucket with governance-mode Object Lock and a default retention set — the arrangement #14 verified. Cheap to do with the same probe.

### Housekeeping

The probe key was a throwaway and its secret reached a terminal transcript, so it should be deleted rather than kept. The pre-existing `pless-machine` key (5 capabilities) leaked the same way in an earlier session and needs replacing too — with a 6-capability key, once the list above is settled in ADR 0017.
--
author:	kschulst
association:	owner
edited:	false
status:	none
--
## Measured: the retention shape, and a dangerous state nobody had named

The #14 bucket is gone — the account holds only `pless-tesst`, with Object Lock off. Since Object Lock cannot be disabled once enabled, that bucket was deleted rather than changed, so ADR 0017's evidence is no longer re-measurable. The ADR stands as the record of it.

So the remaining measurement needed a new bucket, which made it a **dry run of `provision` steps 2 and 3**. Both API calls behaved as proposed.

### `defaultRetention.period` is an object, not a number

```json
"defaultRetention": {
    "mode": "governance",
    "period": { "duration": 90, "unit": "days" }
}
```

So `check_bucket_protection` compares `period.duration` against `version_retention_days` **only after checking `period.unit`**. Assuming days would silently pass a bucket configured in some other unit, which is the class of bug this check exists to prevent. Guessing the shape would have put exactly that in.

And a restricted machine key — bucket-restricted, no `bypassGovernance`, with `readBucketRetentions` — reads all of it:

```
bucketName  : pless-lock-probe
capabilities: ['deleteFiles', 'listBuckets', 'listFiles', 'readBucketRetentions',
               'readFiles', 'writeFiles']
isClientAuthorizedToRead: True
value: { "defaultRetention": { "mode": "governance",
                               "period": { "duration": 90, "unit": "days" } },
         "isFileLockEnabled": true }
```

The target-side check is confirmed workable against a correctly configured bucket, not just against an unconfigured one.

### A fourth state, and it is the dangerous one

`b2_create_bucket` with `fileLockEnabled: true` returns:

```json
{"defaultRetention": {"mode": null, "period": null}, "isFileLockEnabled": true}
```

**Object Lock on, no default retention.** Objects written into that bucket carry no retention at all and can be deleted permanently by the machine's key. It is a reachable, persistent state — it is what every bucket looks like between `b2_create_bucket` and `b2_update_bucket` — and it is exactly what an operator who enabled Object Lock in the console and stopped there would have.

It also looks like success to any check that only asks `isFileLockEnabled`. Which is what the obvious implementation would ask.

So the state table from the previous comment grows a row, and the new row is the one that matters:

| What the target sees | What it means |
|---|---|
| `isClientAuthorizedToRead: false` | the machine key lacks `readBucketRetentions` — a fault in the key, which `provision` prevents |
| `isFileLockEnabled: false` | no Object Lock. CRITICAL |
| `isFileLockEnabled: true`, `defaultRetention.mode: null` | **lock on, nothing retained. CRITICAL, and it looks like success** |
| `mode: "compliance"` | the console's only option. Protects, and binds the operator too — a finding, not a pass |
| `mode: "governance"` + `period` | compare `period.duration` against `version_retention_days`, having checked `period.unit` |

That `provision` sets the retention in a **second** call, after creating the bucket, means the window exists in pless's own code path too: a `provision` that fails between the two leaves the bucket in the dangerous state. It has to verify the result before reporting success, or re-run `b2_update_bucket` idempotently — which also answers the idempotency question from the description: against an existing bucket, `provision` should be able to repair the retention, because that is its own failure mode as much as the console's.

### Cleanup

Probe bucket deleted, both probe keys deleted, local credential files removed. The pre-existing `pless-machine` key (`...0003`, 5 capabilities) is untouched: it leaked into a transcript in an earlier session and should be replaced, but it is the operator's own key and not mine to remove.

### Measurements are done

Nothing in this proposal is now waiting on an API answer. What remains is the ADR for the operator-side provisioning credential, an amendment to ADR 0017 adding `readBucketRetentions`, a rewrite of the canvas's audit section against the table above, and the four smaller questions still open in the description: region, whether `provision` writes `pless.toml` and `.env`, the compliance-mode bucket, and whether the bootstrap `b2_create_key` call is documented or shipped as a script.
--
author:	kschulst
association:	owner
edited:	false
status:	none
--
## The four remaining questions, answered

### 1. Region comes from the API, not from configuration

**No new config key.** `b2_authorize_account` returns the account's S3 endpoint, verified against the real account:

```
apiInfo.storageApi fields: ['absoluteMinimumPartSize', 'apiUrl', 'bucketId', 'bucketName',
                            'capabilities', 'downloadUrl', 'infoType', 'namePrefix',
                            'recommendedPartSize', 's3ApiUrl']

  s3ApiUrl = 'https://s3.eu-central-003.backblazeb2.com'
```

So `provision` assembles `restic_repository` itself as `s3:{s3ApiUrl}/{bucket}`, which is exactly the form [ADR 0017](../blob/main/adr/0017-tamper-resistance-in-the-bucket.md) specifies. It already authenticates, so the endpoint is free.

This is not convenience. A bucket's region is fixed at creation from the account default, so the operator has nothing to choose — a config key would only be another way to get it wrong, in a setup whose whole problem is that mistakes are invisible.

**And it is readable by the machine key**, not just the provisioning one — the probe above used the bucket-restricted machine key. So `pless audit` can also check that the configured `restic_repository` still matches the account's actual endpoint, independently of how it was written.

### 2. Print, do not write — neither `pless.toml` nor `.env`

`pless vm create` writes `[host]` because `pless` owns that section outright. `[backup]` is not like that: twelve keys, explanatory comments, and the operator's own schedule and retention values. `hostfile.apply` replaces a whole section, so writing through it would eat all of that.

More decisive: **a mistyped repository URL is caught immediately** by `pless backup init`, which cannot reach it. That is the opposite of the bucket configuration, where a wrong answer is invisible until the day it matters — and that invisibility is the entire argument for this command. The argument does not transfer to a string whose errors announce themselves.

`.env` is a no for a different reason. The machine key is a secret, `.env` is a cache, and the password manager is the source of truth. Writing it there lets the operator skip the step that matters. `storage init --generate` and `backup init` already set the precedent: show it once, loudly, and let the operator place it. Non-secret configuration is printed; secrets are printed once. #7 is where password-manager integration belongs.

*Rejected alternative:* a `set_key(text, section, key, value)` that edits one line without touching the section. Worth building if several commands come to need it; not for this one.

### 3. A compliance-mode bucket is refused, not repaired

ADR 0017 already establishes that compliance binds the operator as much as an attacker, and that Backblaze's remedy for a period set too long is closing the account. Objects already written under compliance cannot be released.

Whether B2 permits switching a bucket's *default* mode from compliance to governance is **unknown** — and that is the reason to refuse rather than attempt a repair. A repair that half succeeds is worse than a clear no. The message says the bucket has to be replaced, and that an empty one costs nothing to delete.

`pless audit` reports compliance as a finding rather than a pass, which is the row [ADR 0020](../blob/main/adr/0020-the-machine-key-can-read-the-lock.md) already sets out.

### 4. The bootstrap call is documented as a ready-to-paste line, not shipped as a script

No `contrib/` script. ADR 0021's claim — that the master key never reaches `pless` — is cleaner if the repository contains no code that accepts it at all.

But not prose either. A grilling session produced the evidence: the hand-run path failed twice, first on placeholders pasted literally, then on `$EDITOR` being unset. Instructions requiring variable substitution are where people go wrong.

So **`provision`'s refusal prints the finished call**, ready to paste, and the documentation carries the same. Its correctness is checked by re-running `provision`: the refusal *is* the verification. Same shape as `vm create` ending with "Next: `pless bootstrap`", except the next step is a call rather than a command.

## One more thing the verification turned up

`b2_authorize_account` returns `applicationKeyExpirationTimestamp` at the top level. B2 application keys **can carry an expiry**, and a machine key that expires makes backups start failing on a date nobody chose — invisible until a timer fails quietly. On the current key it is `None`.

Two consequences worth folding in: `provision` mints without an expiry, and `pless audit` reports one when it exists, with how long is left. That is cheap, since `audit` already reads this response for the capability check.

## Deferred, not forgotten

The `pless-machine` key (`...0003`) leaked into a transcript and still needs replacing with a six-capability key. Deferred deliberately: the bucket it is restricted to is a test bucket, so the exposure is not meaningful, and the natural moment is the first real installation rather than now.
--

## Domain Concept Identification

### Existing Concepts (from codebase)

- **Repository location** (`config.BackupConfig.restic_repository`): an opaque string restic
  understands — the backup canvas is categorical that `pless` grows no provider abstraction
  around it. `provision` produces its *value*; nothing downstream learns where it came from.
- **Repository kind** (`BackupConfig.repository_kind`): derives `b2`, `s3` or `local` from the
  string's scheme, and is used wherever naming the location would leak a bucket name. Note the
  consequence of ADR 0017: the kind is `s3` even for a Backblaze bucket.
- **Object-storage credentials** (`config.Secrets.b2_key_id`, `b2_application_key`): named as B2
  shows them, translated to restic's names by `backup.render_backup_env`, which is the existing
  translation boundary. `provision` produces these values and, per the settled answer, writes
  them nowhere.
- **Finding / Severity / AuditReport** (`audit.py`): the existing shape of an audit result. Pure
  check functions take collected text and return a `Finding`; no new report type is needed.
- **Host** (`targets.Host`): a machine reachable over SSH. Notable by its **absence** — this is
  the first command whose work involves no host at all.
- **Provider module precedent** (`hetzner.py`): 42 lines, whose docstring reads "A thin wrapper
  around the hcloud SDK. No business logic here." It only verifies a token with read-only calls.
  Nothing in `pless` creates a resource at a provider today.
- **Section writer** (`hostfile.apply`): replaces the `[host]` section wholesale, with a
  `--force` guard against repointing at a different machine. Deliberately **not** reused here:
  `[host]` is a section `pless` owns outright, `[backup]` is not.
- **Generated secrets** (`secretgen`): `human_passphrase` and `machine_token`. Not used by
  `provision`, because B2 mints the machine key — `pless` never chooses it.
- **One-time secret disclosure**: the pattern `storage init --generate` and `backup init` already
  establish. Print once, loudly, and let the operator place it.

### New Concepts Required

- **Provisioning credential**: an operator-side B2 key that can create buckets, set retention and
  mint keys. Its defining property is negative — it must *not* hold `bypassGovernance`, because a
  B2 key cannot grant capabilities it lacks, which is what makes a dangerous machine key
  unobtainable rather than merely unlikely (ADR 0021). Never persisted.
- **Bucket as a provisionable resource**: today a bucket is something the operator obtained
  elsewhere and named in a string. It becomes something `pless` creates, with Object Lock enabled
  at creation because it cannot be added afterwards.
- **Lock configuration**: `fileLockConfiguration` — whether Object Lock is on, the default
  retention `mode` (`governance`, `compliance`, or unset), and a `period` that is an **object**
  (`{"duration": 90, "unit": "days"}`) rather than a count of days. Five distinguishable states,
  set out in ADR 0020.
- **Machine key**: a bucket-restricted key holding exactly six capabilities and no expiry. That
  list is now a shared fact between `provision`, which mints it, `audit`, which checks it, and
  ADR 0017/0020, which justify it.
- **Account endpoint**: `apiInfo.storageApi.s3ApiUrl`. The account states its own S3 endpoint, so
  the region is never configured and never typed.
- **B2 API client**: the first HTTP client in the project. `httpx` is already a declared
  dependency and **imported nowhere** — Hetzner goes through the `hcloud` SDK — so this is a
  greenfield area with a declared tool and no in-repo precedent for its conventions.

### Conceptual Relationships

- A **provisioning credential** creates a **bucket** and mints a **machine key** restricted to
  it. The machine key's capabilities are bounded above by the provisioning credential's, and that
  bound is the mechanism the whole design rests on.
- A **bucket** carries a **lock configuration**. The configuration is what makes the archive
  tamper-resistant; the machine key's `readBucketRetentions` is what makes that checkable.
- `provision` produces a **repository location** and a **machine key**, and the backup path
  consumes both without knowing their provenance. That boundary is the thing to protect.
- **A lifecycle asymmetry worth naming**: Object Lock can be enabled and never disabled, and a
  compliance period can be lengthened and never shortened. A bucket's configuration is a one-way
  door, which is why `provision` must refuse rather than repair in one case and repair rather
  than refuse in another.

### Key Business Rules

- **A credential holding `bypassGovernance` is refused.** Governs the provisioning credential,
  and it is the rule that makes this feature worth building rather than a convenience.
- **The provisioning credential is never persisted** — not `.env`, not `pless.toml`, not argv,
  and not stdout. Governs the credential and the client's error rendering: no request header and
  no authorization token may ever be rendered, because this project settles arguments by pasting
  real API output into issues.
- **The machine key holds exactly the six capabilities, and no expiry.** More is a vulnerability;
  fewer makes the protection unverifiable.
- **`provision` verifies its own result before reporting success.** The lock and the retention are
  set by two calls, so a failure between them leaves the bucket in the state that looks protected
  and is not.
- **Repair the retention, refuse the mode.** An existing bucket missing its default retention is
  `provision`'s own failure mode, so re-running must fix it. A compliance-mode bucket cannot be
  safely repaired, so it is refused with an explanation.
- **Nothing in the backup path learns about B2.** Governs the boundary between this command and
  everything that takes or restores a backup.
- **Implicit rule, surfaced**: the operator never types an endpoint or a region. The account
  states both, so the only thing they choose is a bucket name.

## Strategic Approach

### Solution Direction

A new core module for the provider, a new command namespace in the CLI, and no change to
anything in the backup path.

- **`b2.py`**, a core module in the shape the project already uses: no `typer`, no `rich`, returns
  dataclasses, raises its own error type alongside `BackupError`, `DeployError` and
  `StorageError`. Its distinguishing property is that it takes **no `Host`** and imports neither
  `targets` nor `sshexec` — the first core module that talks outward rather than to a machine.
- **`cli.py` gains a `b2_app` namespace**, registered the way `vm_app` and `hetzner_app` are.
  `provision` is its only command: ADR 0020 settled that the bucket check runs on the target
  inside `pless audit`, so the `pless b2 check` that would otherwise have existed is unnecessary.
- **Data flow**: provisioning credential on stdin, authorize, inspect the credential's own
  capabilities and refuse if over-powered, create or adopt the bucket, set or repair the
  governance retention, mint the machine key, read everything back, then print the key once and
  the repository string.
- **The audit checks follow in a second step, not this one.** They are specified in the backup
  canvas against ADR 0020's state table, and the ordering is deliberate: shipping them first
  would hand every operator a CRITICAL they could only fix with hand-run API calls.

### Key Design Decisions

- **Where the decision logic lives.** `hetzner.py` keeps no business logic and lets `cli.py`
  decide, which works when the decision is "is this token valid". Here the decisions *are* the
  product — which capabilities are acceptable, which of five lock states applies, when to repair
  rather than refuse — and they must be testable without a network or an account. → **Put them in
  `b2.py` as pure functions over parsed responses, with a thin transport seam that tests
  substitute.** Logic in `cli.py` would make the most security-sensitive reasoning in the project
  reachable only through a CLI runner with a live account.
- **How the transport is substituted.** Either a module-level function monkeypatched in tests, or
  an injected callable. → **An injected callable.** The existing fakes-must-match-signatures
  convention shows what monkeypatching costs in vigilance, and `provision` performs a *sequence*
  of calls whose order is itself a safety property worth asserting directly.
- **Whether `provision` writes configuration.** → **Print, do not write.** A mistyped repository
  string is caught immediately by `pless backup init`, whereas the bucket misconfiguration this
  command exists to prevent is invisible until it matters; the argument for writing does not
  transfer. And `.env` is a cache whose source of truth is a password manager, so writing the key
  there lets the operator skip the step that matters. #7 owns that integration.
- **Whether the bootstrap call ships as code.** → **Documented as a ready-to-paste line emitted by
  the refusal itself.** ADR 0021's claim that the master key never reaches `pless` is cleaner if
  the repository contains no code that accepts it, but prose with placeholders to substitute is
  where operators go wrong — observed twice in one grilling session. Re-running `provision` is the
  verification.
- **Idempotency.** → **Adopt and repair, do not refuse.** An existing bucket with Object Lock and
  no default retention is exactly what a half-completed `provision` leaves behind, so refusing
  would strand the operator in the dangerous state with no way forward.

### Alternatives Considered

- **Document the three API calls and ship no command**: the documentation then carries the same
  maintenance surface as code, without tests and without anything checking the result — for the
  step that decides whether the archive can be destroyed. Rejected in ADR 0021.
- **A `--from-master` flag**: brings the whole account's blast radius into the tool, for a
  credential needed once per account. Rejected in ADR 0021; the likely leak path is output rather
  than argv.
- **`pless backup provision`**: puts provider knowledge inside the namespace that exists to have
  none, and would be the only `pless backup` subcommand resolving no host.
- **A `pless b2 check` alongside `provision`**: made unnecessary by ADR 0020, since the machine
  key can read the lock and the check belongs where the operator already looks.
- **Driving Backblaze's own CLI as a subprocess**: a second tool to install and version, and it
  still requires knowing which calls to make in what order. That knowledge is the deliverable.

## Risk & Gap Analysis

### Requirement Ambiguities

- **Which bucket name, and who chooses it.** The requirement says `provision` creates the bucket
  but never says how it is named — a flag, a prompt, or derived from configuration. There is no
  obvious default: `pless.toml` holds no bucket name, only a repository string that does not exist
  yet.
- **What happens when the machine key already exists.** Re-running mints a second key rather than
  rotating, and B2 permits duplicate key names, so repeated runs accumulate valid keys restricted
  to the bucket. The requirement is silent — and the deferred rotation of the leaked
  `pless-machine` key means this gets exercised early.
- **Whether `provision` is meant to be runnable unattended.** "Never persisted" means a credential
  on stdin every time, which rules out a cron or CI invocation. Nothing in the requirement wants
  that, but the web wizard in #8 eventually will.

### Edge Cases

- **B2 bucket names are globally unique across all accounts.** `b2_create_bucket` with a plausible
  name like `paperless` will very likely fail because a stranger holds it. The raw API error does
  not distinguish "taken by you" — which `provision` should adopt and repair — from "taken by
  someone else", which needs a different name. Conflating them would tell an operator to rename a
  bucket they already own, or to try adopting one they cannot see.
- **A bucket that exists without Object Lock.** The lock cannot be added later, so this is
  unrepairable, and it sits immediately beside the missing-retention case that *is* repairable.
  Two adjacent states, opposite answers.
- **Adopting a non-empty bucket.** Repairing the retention on a bucket that already holds objects
  leaves those objects unprotected, because a default retention applies only to objects written
  after it is set. A report of success would be true and misleading.
- **The provisioning credential cannot be inspected.** If `b2_authorize_account` fails, the
  credential's capabilities are unknown, so the refusal cannot be evaluated. The only safe reading
  is to stop — never to proceed on the assumption that an unverifiable credential is acceptable.
- **A credential already restricted to one bucket, used to provision another.** `b2_create_key`
  would fail, but late and obscurely.
- **A retention period in a unit other than days.** Measured to be an object carrying a unit; a
  bucket configured elsewhere could use another one.

### Technical Risks

- **A two-call window inside `pless`.** Creating the bucket and setting its retention are separate
  calls, so an interruption leaves the state that looks protected and is not. Mitigation is in
  ADR 0021: read the result back before reporting success, and make re-running repair it.
- **First HTTP client in the project.** `httpx` is declared but imported nowhere, so there is no
  in-repo convention for timeouts, retries or error rendering to follow — and here the
  error-rendering rule is a security requirement rather than a nicety. Mitigation: make the
  rendering path a named, tested function rather than an f-string at the call site.
- **Secrets reaching output.** The failure mode is not argv but a rendered error or a debug dump.
  Mitigation: reconstruct errors from the status code and B2's own error body, and assert in tests
  that no credential or token appears in a rendered error.
- **A surface that is untestable by default.** Every behaviour that matters involves a provider
  API. The transport seam addresses it; the residual risk is fakes drifting from what B2 actually
  returns, which this project has been bitten by before. The measurements on #16 are real captured
  responses and should become the fixtures.
- **B2 API v3 shape drift.** `apiInfo.storageApi` is a v3 nesting that did not exist in v2, and
  the probes on #16 handled both shapes defensively. Pinning to v3 is right, but parsing should
  not assume fields observed only once.
- **No drill without an account.** Unlike every stage of the backup work, this cannot be verified
  on a disposable VM: it needs a real Backblaze account and creates real resources. The
  measurements on #16 are the closest thing to a drill, and a verification run will cost a
  throwaway bucket.

### Acceptance Criteria Coverage

| AC# | Description | Addressable? | Gaps/Notes |
|-----|-------------|--------------|------------|
| 1 | Refuse a credential holding `bypassGovernance`, printing the `b2_create_key` call that mints a correct one | Yes | The load-bearing property (ADR 0021). Must also refuse when the credential cannot be inspected at all |
| 2 | Create the bucket with Object Lock enabled | Partial | The bucket-name question and B2's global namespace are unresolved; "exists without Object Lock" is unrepairable and needs its own message |
| 3 | Set the default retention to governance for `version_retention_days` | Yes | The period is an object carrying a unit; measured |
| 4 | Mint a machine key restricted to the bucket with exactly the ADR 0017 capabilities | Yes | Six capabilities per ADR 0020, and no expiry. Repeated runs accumulating keys is unresolved |
| 5 | Print the machine key once, and the `restic_repository` value | Yes | Endpoint from `s3ApiUrl`; nothing written to `pless.toml` or `.env` |
| 6 | Idempotent against an existing bucket — repair the retention | Yes | Adopting a non-empty bucket needs an honest message about objects already written |
| 7 | Refuse a compliance-mode bucket | Yes | Whether B2 permits switching the mode is unknown, which is the reason to refuse rather than attempt it |
| 8 | Never render request headers or the authorization token | Yes | A security requirement, so it belongs in a tested rendering function |
| 9 | Verify the result before reporting success | Yes | Closes `provision`'s own two-call window |
| 10 | `pless audit` reports the credential and bucket findings | Deferred | Specified in the backup canvas against ADR 0020's table. Deliberately after this work, so no operator is handed a CRITICAL they can only fix by hand |

Ten of ten addressable: one deferred by design, one partial pending the bucket-naming decision.

### Questions the canvas must answer

Design choices, not unknowns about the world:

1. How the bucket is named — flag, prompt, or derived — and what validation `pless` applies before
   B2 rejects it.
2. How "taken by you" is distinguished from "taken globally", given that B2 cannot list a bucket
   the credential is not allowed to see.
3. What a re-run does when it would mint a second machine key.
4. Whether adopting a non-empty bucket is permitted, and what it says if so.
