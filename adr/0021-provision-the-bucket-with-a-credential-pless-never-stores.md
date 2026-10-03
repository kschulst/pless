# 0021 — Provision the bucket from pless, with a credential it never stores

- **Status:** Accepted
- **Date:** 2026-10-03
- **Context references:** issues #14 and #16; follows
  [0017](0017-tamper-resistance-in-the-bucket.md) and [0020](0020-the-machine-key-can-read-the-lock.md)

## Context

[ADR 0017](0017-tamper-resistance-in-the-bucket.md) found that Backblaze's web console cannot
produce the setup this design depends on, in two independent ways. A console key marked "Read and
Write" comes back holding all 29 capabilities — including `bypassGovernance`, the one that defeats
Object Lock — and ignores the bucket restriction that was selected. And the create-bucket dialog
offers Object Lock as "Compliance mode only", while governance is reachable only through
`b2_update_bucket`.

So the setup is API-first whether or not `pless` helps. The alternative to a command is
documentation reading *open a terminal, install a CLI, and make these three API calls* — for the
step that decides whether the backups can be destroyed. That is a setup people get wrong, and
wrong here is invisible until the day it matters.

The obstacle is the credential. Provisioning needs `writeBuckets`, `writeBucketRetentions` and
`writeKeys`, and a B2 key cannot grant capabilities it does not itself hold — so a key that can
mint the machine key must also hold that key's read, write and delete rights. That is a powerful
credential, on a laptop, in a design whose [ADR 0005](0005-the-local-network-is-hostile.md) assumes
the local network is hostile.

## Decision

**`pless b2 provision`** creates the bucket with Object Lock, sets a governance default retention
from `[backup] version_retention_days`, and mints a machine key restricted to that bucket with the
capabilities [0017](0017-tamper-resistance-in-the-bucket.md) and
[0020](0020-the-machine-key-can-read-the-lock.md) specify.

Three properties make it acceptable:

**The credential arrives on stdin and is never persisted.** Not in `.env`, not in `pless.toml`,
not in argv — the same reasoning as [ADR 0014](0014-secrets-never-in-argv.md), extended to stdout,
because a terminal is a transcript and transcripts get pasted into issues. The B2 client never
renders request headers or the authorization token for the same reason.

**It refuses a credential that holds `bypassGovernance`.** The master key therefore never reaches
`pless` at all. The refusal prints the one `b2_create_key` call that mints a correct provisioning
key.

**It lives under a provider namespace, not under `pless backup`.** Every `pless backup`
subcommand resolves a host and never learns which provider holds the repository; this one does
neither. It is [ADR 0016](0016-there-is-only-a-host.md)'s shape applied to storage: `pless vm
create` is a way to obtain a *host* while `[host]` stays generic, and this is a way to obtain a
*bucket* while `restic_repository` stays an opaque string.

## Consequences

**A dangerous machine key becomes unobtainable rather than unlikely.** Because B2 refuses to issue
capabilities the parent key lacks, a provisioning key without `bypassGovernance` cannot mint a
machine key with it — not even by mistake, and not if this code is wrong. The guarantee moves from
"pless remembered to omit a flag" to "the service will not issue it", which is the same move 0017
made when it stopped relying on withholding delete.

That is the whole justification. Without the refusal, `provision` would merely *promise* to omit
the capability, and a promise is what 0017 ruled insufficient.

**A bootstrap step appears.** The operator mints a provisioning key once per account, with one
documented API call. Unlike the bucket, a mistake there is caught immediately: `provision`'s own
refusal inspects the credential before doing anything. The console's failure is invisible; this
one is not.

**`provision` has the console's dangerous failure mode too.** Object Lock is enabled at bucket
creation and the retention is set by a second call, so a run that fails between them leaves a
bucket with the lock on and nothing retained — which [0020](0020-the-machine-key-can-read-the-lock.md)
identifies as the state that looks like success. `provision` therefore reads the result back before
reporting success, and re-running it against an existing bucket repairs the retention rather than
refusing. Its own failure mode is the reason it must be idempotent.

**`pless` grows a provider-specific command.** The surface is bounded: nothing in the path that
takes or restores a backup learns about B2, and the repository stays a string restic understands.
This is also the first command in `pless` that creates resources at a provider —
`pless hetzner` only verifies a token — so the pattern it sets will be copied, and
[#5](https://github.com/kschulst/pless/issues/5) should follow it rather than invent another.

It makes a setup wizard possible. Click-ops cannot be wizarded, and here the clicks produce the
wrong answer anyway.

## Alternatives considered

- **Document the three API calls and ship no command:** the documentation then has the same
  maintenance surface as code, without tests and without anything checking the result — for the
  step that decides whether the archive can be destroyed. It also leaves the console's confidently
  wrong answer as the path of least resistance.
- **A `--from-master` flag that mints the provisioning key for you:** convenient, and it brings the
  entire account's blast radius into the tool. The master key cannot be restricted, there is one
  per account, and it holds `bypassGovernance`. The likely leak path is not argv but output:
  `b2_authorize_account` sends it as HTTP Basic auth and answers with a bearer token carrying its
  full capabilities for 24 hours, and this project settles arguments by pasting real API output
  into issues. One documented call, checked by the next command, is a better trade.
- **Persist the provisioning credential in `.env` so `provision` can re-run unattended:** makes the
  laptop's safety load-bearing. "Not persisted anywhere" is mechanically checkable; "the laptop is
  not exposed" is an argument that invites counter-examples — theft, malware, a synced backup.
- **`pless backup provision`:** puts provider knowledge inside the namespace that exists to have
  none, and would be the only `pless backup` subcommand that resolves no host.
- **Let the operator use Backblaze's own CLI:** a second tool to install and version, and it still
  requires knowing which three calls to make and in what order. That knowledge is the deliverable.
