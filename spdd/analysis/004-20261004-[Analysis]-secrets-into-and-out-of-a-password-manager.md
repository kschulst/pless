# SPDD Analysis: Secrets into and out of a password manager

## Original Business Requirement

> **Write generated secrets straight into a password manager** ([#7](https://github.com/kschulst/pless/issues/7))
>
> `pless init --secrets` generates strong secrets into `.env` and then tells the operator to
> copy them into a password manager by hand. That manual step is the most error-prone part of
> setup, and the consequences of skipping it are severe — `.env` is a cache, and a lost
> passphrase is unrecoverable.
>
> **Idea:** when a supported password manager CLI is present and unlocked, offer to write the
> secrets directly.
>
> **Worth investigating:** Bitwarden (`bw`), 1Password (`op`), pass, and the macOS keychain
> (`security`). Each has a different unlock model, and several require an unlocked session that
> `pless` should detect rather than trigger.
>
> **Constraints**
> - Strictly optional; `pless` must work with no password manager installed
> - Never prompts for a master password — detect an unlocked session or decline
> - Secrets go on stdin, never in argv
>   ([ADR 0014](../../adr/0014-secrets-never-in-argv.md))
> - The LUKS passphrase is chosen by the operator, not generated, so it needs a different flow

## Domain Concept Identification

### Existing Concepts (from codebase)

- **A generated secret** — `secretgen` produces two kinds, and the distinction is already
  load-bearing. `machine_token()` is 256 bits of base64url that nobody ever types;
  `human_passphrase()` is 125 bits in five Crockford groups, for the two secrets an operator may
  one day read off paper.
- **`.env` as a cache, not a source of truth** — the template says so in its first sentence, and
  `config.Secrets` reads from it. Every doc page repeats it. The password manager is already the
  declared source of truth; what is missing is any mechanism that makes that true.
- **`pless init --secrets`** — generates exactly three secrets (`PAPERLESS_ADMIN_PASSWORD`,
  `PAPERLESS_SECRET_KEY`, `POSTGRES_PASSWORD`) into `.env`, skipping any key already set, and then
  prints a reminder in bold yellow. The reminder is the whole of the current "integration".
- **`pless secrets generate`** — prints **one** secret and nothing else, with a comment in the
  source stating the intent: *"Deliberately not console.print: no markup, no wrapping and no
  colour, so this can be piped straight into a password manager's CLI."*
- **The LUKS passphrase** — chosen by the operator, never written to disk anywhere, deliberately
  absent from `.env.example`, and the subject of
  [ADR 0018](../../adr/0018-no-plaintext-copy-on-the-operators-machine.md).
- **Reading secrets at runtime** — settled by
  [ADR 0022](../../adr/0022-secrets-resolve-through-a-command-not-an-integration.md): `X_COMMAND`
  beside `X`, run it, read stdout, know nothing about any vault.

### New Concepts Required

- **A store operation** — putting a named secret into whatever the operator uses, as the inverse
  of ADR 0022's read. It does not exist in any form.
- **A vault entry name** — the coordinate a secret is stored under and later read back from. `.env`
  has keys; a vault has its own naming, and something has to decide the mapping.

### Key Business Rules

- **`pless` works with no password manager at all.** Every path here is additive.
- **A secret never appears in argv** ([ADR 0014](../../adr/0014-secrets-never-in-argv.md)), which
  constrains how a value reaches any external command: stdin, always.
- **Nothing prompts for a master password.** An unlocked session is used or the operation declines.
- **Two secrets are unrecoverable** — the LUKS passphrase and `RESTIC_PASSWORD`. Everything here
  matters most for them and is least able to help with the first, which is never generated.

## Strategic Approach

### Solution Direction

**The issue as written and the ADR just accepted point in opposite directions, and that is the
thing to settle first.**

#7 is about **writing**: generated secrets should land in a vault instead of being copied by hand.
[ADR 0022](../../adr/0022-secrets-resolve-through-a-command-not-an-integration.md) settled
**reading**: `pless` resolves `X_COMMAND` at runtime and knows nothing about any vault. Both are
"password manager integration" and they share almost nothing — different trigger, different
direction, different failure modes. The discussion on #7 drifted entirely onto the read half, so
the write half is still untouched.

Two measurements narrow the write half considerably.

**The two secrets that matter most are already composable.** `pless secrets generate` exists for
exactly this, and says so in its own source:

```sh
pless secrets generate --kind human -q | op item create --category password \
  --title pless-restic password=-
```

That works today, for `RESTIC_PASSWORD` and for a LUKS passphrase the operator chooses to generate.
No `pless` change is involved.

**The actual gap is three secrets wide.** `init --secrets` generates
`PAPERLESS_ADMIN_PASSWORD`, `PAPERLESS_SECRET_KEY` and `POSTGRES_PASSWORD` *inside* the command and
writes them into `.env`. They never pass through a pipe, so there is no composition path for them —
and all the operator gets is a bold reminder, which is advice rather than a mechanism. These three
are also the least consequential of the nine: each can be reset by whoever controls the service it
belongs to.

So the honest framing is that #7 proposes to automate the safest part of a manual step whose
dangerous part is already automatable. That is worth doing, but it is not the priority the issue's
severity language implies.

### Key Design Decisions

- **Which direction is in scope** — read, write, or both.
  *Trade-offs:* read is decided and unbuilt; write is undecided and nearly unnecessary.
  → **Recommendation: build the read half first** (ADR 0022's `X_COMMAND`), and treat the write
  half as a second, smaller change. Reading is what makes `.env` stop holding secrets at all, which
  is the outcome #7's opening paragraph actually describes. Writing a secret into a vault that
  `pless` then cannot read from leaves the operator with two places to maintain.

- **Per-tool support versus one command** — the issue names four CLIs and their unlock models.
  *Trade-offs:* detecting an unlocked `op`, `bw`, `pass` or `security` session requires knowing each
  tool's probe (`op whoami`, `bw status`, a gpg-agent query, keychain state). That is per-tool code
  and per-tool breakage, which is **precisely what ADR 0022 declined** for the read path, for
  reasons that apply unchanged here.
  → **Recommendation: one store command, mirroring the read command.** The operator supplies
  something that takes a name and reads a value on stdin; `pless` runs it. Symmetry with
  `X_COMMAND`, neutrality preserved, ADR 0014 satisfied by construction, and the unlock model
  becomes the operator's business — which is the only place it can correctly live, since only they
  know whether their session is unlocked and what to do if it is not.

- **What `init --secrets` writes into `.env`** — the value, or the command that retrieves it.
  *Trade-offs:* writing the value keeps today's behaviour and leaves a plaintext secret on disk,
  which is the thing ADR 0022 exists to end. Writing the command means `.env` holds coordinates
  only.
  → **Recommendation: when a store command is configured, write the command and not the value.**
  This is the shape that makes the two halves one feature instead of two, and it is the only
  version where "`.env` is a cache" becomes structurally true rather than aspirational.

- **The LUKS passphrase stays outside** — it is chosen rather than generated, never written to disk
  ([ADR 0018](../../adr/0018-no-plaintext-copy-on-the-operators-machine.md)), and absent from
  `.env.example` on purpose. The most `pless` can offer is what it already offers: generate one on
  stdout for the operator to pipe where they like.
  → **Recommendation: no change, and say so in the record.** A flow that stored it would be the
  plaintext copy 0018 forbids.

### Alternatives Considered

- **Ship support for `op`, `bw`, `pass` and `security` directly:** what the issue proposes. It
  means four unlock probes, four naming conventions, four CLIs whose flags change, and a choice
  made on behalf of every operator using a fifth thing. ADR 0022 rejected this for reading two days
  ago; nothing about writing makes it a better trade.
- **Do nothing and document the pipe:** defensible, and cheaper than it sounds — it covers both
  unrecoverable secrets and needs no code. It leaves the three `init --secrets` values with no path
  except manual copying, which is the gap the issue is actually about.
- **Have `init --secrets` print the three values on stdout for piping, instead of writing `.env`:**
  composable and consistent with `secrets generate`, but it breaks a command whose current job is to
  produce a working `.env`, and three secrets cannot be piped to three different places in one
  invocation.
- **Write the secrets into the vault and leave `.env` untouched:** then the operator has the value
  in two places and `pless` reads the less safe one. Worse than either end state.

## Risk & Gap Analysis

### Requirement Ambiguities

- **Read or write.** The headline. The issue's title and body describe writing; its comment thread
  and ADR 0022 describe reading. They need different work and the issue should probably become two.
- **Which secrets.** "Generated secrets" is three of nine. `RESTIC_PASSWORD` is not generated by
  `init --secrets` at all, yet it is the one the issue's severity language is about.
- **What "offer to" means.** An interactive prompt during `init`, a flag, or a config key. Unstated,
  and it decides whether this works unattended.
- **The vault entry name.** Nothing says whether `pless` chooses it, the operator does, or it is
  derived from the `.env` key. A wrong guess here is what makes a later read fail.

### Edge Cases

- **An entry already exists under that name.** Overwriting is the severe case: if a stored
  `RESTIC_PASSWORD` is replaced with a freshly generated one, **the backup becomes unopenable and
  the only copy of the real passphrase is gone.** This cannot be the default behaviour, and
  detecting it requires asking the vault — which a neutral store command cannot do on `pless`'s
  behalf. Likely resolution: the store command is responsible, and `pless` documents that it must
  refuse to overwrite.
- **Storing succeeds, writing `.env` fails** (or the reverse). Ordering decides whether the operator
  ends with a secret they cannot find or a reference to one that is not there.
- **The store command fails after the secret is generated.** The value then exists only in memory
  and possibly in scrollback. Either it is shown so it is not lost, which contradicts ADR 0018's
  spirit, or it is discarded and regenerated, which is fine for these three and would not be for
  `RESTIC_PASSWORD`.
- **Re-running `init --secrets`.** Currently idempotent: it fills only empty keys. With a store
  command, "empty" becomes ambiguous — a key holding a `_COMMAND` reference is not unset.
- **A locked session.** Declining is specified. What happens next is not: abort the whole `init`, or
  fall back to writing `.env` and the reminder.

### Technical Risks

- **Per-tool unlock detection ages badly.** `op`, `bw` and `pass` change their CLIs, and a probe
  that silently stops working would make `pless` decline forever or, worse, believe a locked session
  is unlocked. The one-command direction avoids this entirely.
- **A store command is a code-execution seam.** `X_COMMAND` already is one; both run a string from
  `.env` through a shell. That is the operator's own file, so the trust boundary is acceptable, but
  it must be stated rather than discovered.
- **Secrets in a subprocess environment are visible to that subprocess' children.** Passing on stdin
  (ADR 0014) handles argv; the environment is a separate question for whatever the store command
  then does, and outside `pless`'s control.
- **No integration test without a vault.** Both directions need a test double that is a command, not
  a library — which is also the argument for the design, since a fake is then a three-line script.

### Acceptance Criteria Coverage

#7 states constraints rather than acceptance criteria. Assessed as such:

| # | Constraint | Addressable? | Gaps/Notes |
|---|---|---|---|
| 1 | Strictly optional; works with no password manager installed | Yes | Both directions are additive; absent `X_COMMAND` or a store command, behaviour is unchanged |
| 2 | Never prompts for a master password — detect an unlocked session or decline | Partial | Detection is per-tool, which conflicts with ADR 0022's neutrality. Resolved by moving the responsibility into the operator's command — but then `pless` cannot *guarantee* the constraint, only document it |
| 3 | Secrets on stdin, never argv | Yes | Satisfied by construction if the store command reads stdin; the read path already satisfies it, since stdout is not argv |
| 4 | The LUKS passphrase needs a different flow | Yes | Recommendation is **no** flow: it is never generated by `pless`, never written to disk, and `secrets generate --kind human` already covers generating one |

### What this analysis recommends before any canvas

1. **Split #7.** One issue for reading (`X_COMMAND`, decided by ADR 0022, unbuilt) and one for
   writing (undecided, three secrets, much smaller). The current issue conflates them and its title
   describes the smaller half.
2. **Build the read half first.** It is what makes `.env` stop holding secrets, it is already
   decided, and it needs no vault-specific code.
3. **Settle the overwrite question explicitly** before the write half gets a canvas, because the
   failure mode is an unopenable backup and the mechanism cannot prevent it unaided.
