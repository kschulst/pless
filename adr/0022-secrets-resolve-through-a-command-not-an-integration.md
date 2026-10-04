# 0022 — Secrets resolve through a command, not an integration

- **Status:** Accepted
- **Date:** 2026-10-04
- **Context references:** issue #7; extends
  [0014](0014-secrets-never-in-argv.md); constrained by
  [0001](0001-encrypt-data-key-never-on-the-machine.md) and
  [0005](0005-the-local-network-is-hostile.md)

## Context

`pless` needs nine secrets, and `config.Secrets` reads all of them from the environment or a `.env`
file. Two of them are consequential beyond the usual: `B2_APPLICATION_KEY`, which can write to the
backup bucket, and `RESTIC_PASSWORD`, **without which no backup can ever be opened again.** There
is no reset, no recovery and no support channel. Lose it and the off-site copy becomes a few
gigabytes of noise.

So they sit in plaintext on the operator's laptop — the machine [ADR
0005](0005-the-local-network-is-hostile.md) assumes is on a hostile network, and the one [ADR
0001](0001-encrypt-data-key-never-on-the-machine.md) keeps the LUKS key away from the *target* for
exactly this class of reason. A laptop is stolen, a laptop is backed up to a cloud that is itself
breached, a laptop runs an npm postinstall script. `.env` is in `.gitignore`, which prevents the
one leak path people think of and none of the others.

[ADR 0014](0014-secrets-never-in-argv.md) settled how a secret *travels* — stdin, never argv,
because `ps` is readable by any local user. It said nothing about where it rests.

Four candidate tools were weighed, and they are not the same kind of thing:

- **A hosted secrets service** (envsecrets and its peers) encrypts end-to-end and stores
  centrally. It needs an account and a network round trip to read a secret.
- **Vault or OpenBao** is self-hostable. Both start **sealed** and must be unsealed after every
  restart; OpenBao's documentation says auto-unseal "delegates the responsibility of securing the
  unseal key from users to a trusted device or service" — an external KMS or HSM.
- **sops with age** encrypts a file of secrets and decrypts to stdout. No server, no account, no
  network. The age identity can live in the OS keyring or on hardware.
- **dotenvx** encrypts values inside `.env` itself and decrypts with `DOTENV_PRIVATE_KEY`.

A measurement that matters: **the last two already work, with no support from `pless` at all.**

```sh
sops exec-env secrets.yaml 'pless backup run'
dotenvx run -- pless backup run
```

Both are wrappers. They put decrypted values in the environment of a child process, and
`config.Secrets` reads the environment. Nothing needs to change for an operator to adopt either
today. That rules out urgency as a reason to build anything, and narrows the question to what a
wrapper *cannot* do: resolve **one** secret from **one** place, when the others come from
elsewhere — which is exactly the shape of the OS keyring, where there is no wrapper to run.

## Decision

**For every secret `X`, `pless` also accepts `X_COMMAND`. It runs that command, reads stdout, and
uses the result. It knows nothing about any vault.**

```sh
# .env — coordinates, not secrets
RESTIC_PASSWORD_COMMAND=security find-generic-password -s pless-restic -w
B2_APPLICATION_KEY_COMMAND=sops -d --extract '["b2"]["key"]' ~/secrets.yaml
PAPERLESS_API_TOKEN_COMMAND=pass show pless/paperless-token
```

Four things recommend this over integrating anything.

**It is restic's own convention, not an invention.** `RESTIC_PASSWORD_COMMAND` already exists and
already means this. Adopting the pattern for the other eight secrets makes one rule instead of two,
and the one rule is already documented by someone else.

**The vault choice is personal, and `pless.toml` is not.** `CLAUDE.md` requires `pless.toml` to
stay neutral — no personal paths, no one operator's setup. Vault coordinates are precisely that
kind of value, and `.env` is already the local, personal, git-ignored file. The coordinates go
where the secrets used to be; what changes is that they are no longer secret.

**Every vault works, including ones that do not exist yet.** 1Password, Bitwarden, `pass`, `age`,
sops, gopass, the macOS Keychain, libsecret, a YubiKey, OpenBao for someone who already runs it,
and the tool that replaces all of them in four years. `pless` ships support for none and works with
all.

**It is a dozen lines and fully testable.** Run a subprocess, strip the trailing newline, use the
output. No client library, no network code, no mocking a vault to test it.

### What is excluded, and why

**A hosted service is not an acceptable default.** `pless` exists so that the archive lives on
hardware the operator holds and the backup survives a provider suspending the account — the
argument of [0013](0013-backup-offsite-and-drilled.md) and
[0017](0017-tamper-resistance-in-the-bucket.md). Needing a third party's account and a working
network to read `RESTIC_PASSWORD` contradicts that at the one point where it is least recoverable.
Nothing stops an operator pointing a command at one; it must not be what the documentation
suggests.

**Vault and OpenBao are the wrong size, and they double a problem we already have.**
[ADR 0003](0003-manual-unlock-after-boot.md) already accepts a manual unlock after every boot,
because the key is deliberately not on the machine. A vault adds a *second* thing to unseal, whose
key shares must then live somewhere — and both answers are circular. On the target, the machine
holds the secrets protecting the machine, which is what [0001](0001-encrypt-data-key-never-on-the-machine.md)
exists to prevent. On the laptop, it is a server daemon on something that sleeps and changes
networks. Auto-unseal resolves the circle by depending on an external KMS, which is the first
exclusion again. These tools are right for many machines, many services, rotation policies and an
audit log; that problem is real and it is not this one.

**`pless` will not bundle one vault's client.** Shipping support for a particular tool means
choosing for every operator, carrying its API's breakage, and being wrong for whoever uses
something else. Being neutral is cheaper and better.

## Consequences

**`X` and `X_COMMAND` together is an error, not a precedence rule.** This is the decision most
likely to be questioned, so: a precedence rule means a stale plaintext secret sits in `.env` being
silently ignored, while the operator believes it is in use — and believes they have rotated
something they have not. An error costs one confused minute, once. Silence costs a false belief
about where a credential lives.

**A failing command is fatal, and never an empty secret.** An empty `RESTIC_PASSWORD` is not a
missing password: `restic init` would happily create a repository with it. Treating a failed
resolution as "no value" would mean a typo in a keychain lookup silently producing a *second*
repository that the real passphrase cannot open. The command's exit code is checked, and a non-zero
one stops `pless` with the command and its stderr.

**Resolved secrets are still never rendered.** [0014](0014-secrets-never-in-argv.md) applies
unchanged, and gains a clause: the *command* may appear in a message, because it is coordinates,
but its output may not. `B2_APPLICATION_KEY_COMMAND=pass show …` is safe to print in an error; what
`pass` wrote to stdout is not.

**The command runs on the operator's machine, and runs often.** A `pless` invocation that needs
four secrets runs four commands, each possibly prompting for a keyring unlock. That is the cost of
per-secret resolution, and it is the reason the wrapper form stays documented beside it: a single
`sops exec-env` decrypts once. Both are supported; neither is mandatory.

**This does not solve `RESTIC_PASSWORD`.** Encrypting it with a key on the same laptop moves the
problem without removing it, and no vault helps if the laptop is lost with both. **The passphrase
needs an offline copy that does not depend on any computer** — printed, in a safe, or written in a
notebook. The documentation must say so plainly, because this is the one secret whose loss is
unrecoverable and the one an operator is most likely to believe is handled.

**Nothing in the code learns what a vault is.** `config.Secrets` gains a resolution step; no other
module changes, and `pless.toml` stays free of anyone's personal paths.

## Alternatives considered

- **A `[secrets]` section in `pless.toml` naming a backend and its coordinates:** the first shape
  proposed, and worse. It puts one operator's vault paths into the file this project keeps neutral,
  and it invents a schema where restic already has a convention. Two places to look instead of one.
- **sops + age as a documented dependency rather than an example:** the best of the tools surveyed
  — files, no server, offline, and the identity can sit in hardware. Still a choice made on
  someone's behalf, and it already works as a wrapper without being named. It belongs in the
  cookbook as the suggestion, which is where a recommendation can be revised without an ADR.
- **dotenvx as the blessed mechanism:** keeps the familiar `.env` shape, which is genuinely
  appealing. But its private key defaults to `.env.keys` beside the file it protects, so against
  the threats that motivate this — a stolen laptop, a synced backup, a malicious postinstall — it
  protects the *shared* file and not the local disk, unless the key goes in a keyring anyway. And
  it needs no decision from `pless` to be used.
- **Encrypt `.env` ourselves, with a passphrase prompt:** a bespoke crypto format in a tool whose
  job is elsewhere, competing with four mature options, and one more thing whose failure mode is
  an unopenable backup.
- **Leave it: `.env` with 0600 and a note in the docs:** the status quo, and defensible for a
  laptop with FileVault — the disk is encrypted at rest, which answers theft but not a process
  running as the operator. It is also not a decision, and the question has now been asked three
  times, which is the signal that it should be written down rather than re-litigated.
