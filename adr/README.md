# Architecture decision records

Why `pless` is built the way it is. Each record captures one decision, the forces behind it,
and what it costs — so a contributor can tell a deliberate choice from an accident, and knows
what would have to change for it to be revisited.

These live beside the code rather than on the [documentation site](https://kschulst.github.io/pless/),
which is for people *using* `pless`.

## Register

| # | Decision | Status |
|---|---|---|
| [0001](0001-encrypt-data-key-never-on-the-machine.md) | Encrypt the data volume, and never store the key on the machine | Accepted |
| [0002](0002-cipher-chosen-automatically.md) | Choose the LUKS cipher automatically from the CPU | Accepted |
| [0003](0003-manual-unlock-after-boot.md) | Unlock manually after every boot | Accepted |
| [0004](0004-access-through-tailscale-only.md) | Reach the archive through Tailscale, not the public internet | Accepted |
| [0005](0005-the-local-network-is-hostile.md) | Treat the local network as hostile | Accepted |
| [0006](0006-harden-is-a-guarded-step.md) | Hardening is a separate step that refuses to lock you out | Accepted |
| [0007](0007-verify-exposure-mechanically.md) | Verify exposure mechanically, because Docker bypasses the firewall | Accepted |
| [0008](0008-one-host-spec-two-delivery-mechanisms.md) | One host specification, two delivery mechanisms | Accepted |
| [0009](0009-cli-is-a-library-with-a-terminal-frontend.md) | The CLI is a library with a terminal front-end | Accepted |
| [0010](0010-data-in-an-encrypted-file-not-a-partition.md) | Put the data in an encrypted file, not a partition | Accepted |
| [0011](0011-debian-and-ubuntu-as-equals.md) | Support Debian and Ubuntu as equals, with Debian 13 as the floor | Accepted |
| [0012](0012-import-through-the-rest-api.md) | Import through the Paperless REST API, not the consume folder | Accepted |
| [0013](0013-backup-offsite-and-drilled.md) | Back up off-site, at a different provider, and drill the restore | Accepted, amended by 0017 and 0018 |
| [0014](0014-secrets-never-in-argv.md) | Pass secrets on stdin, never in argv | Accepted |
| [0015](0015-documentation-drift-is-a-build-failure.md) | Documentation drift is a build failure | Accepted |
| [0016](0016-there-is-only-a-host.md) | There is only a host; provisioning is a separate, optional step | Accepted |
| [0017](0017-tamper-resistance-in-the-bucket.md) | Tamper resistance lives in the bucket, not in the credential | Accepted |
| [0018](0018-no-plaintext-copy-on-the-operators-machine.md) | A plaintext local copy is not a backup layer | Accepted |
| [0019](0019-the-verification-record-lives-on-the-target.md) | The verification record lives on the target, and names a snapshot | Accepted |

## Writing a new one

Copy [`0000-template.md`](0000-template.md) to the next number, using a kebab-case title that
says what was decided rather than what was discussed. Add a row to the register above — a test
enforces that.

Records are append-only. When a decision is replaced, leave the old record in place, set its
status to `Superseded by NNNN`, and say in the new one what it supersedes. The reasoning that
turned out to be wrong is often the most useful part to a future reader.

Write an ADR when a choice would surprise someone reading the code, when it closes off an
obvious alternative, or when you had to argue yourself out of the tempting option. Routine
choices do not need one.

## Where the rest lives

- **What is being worked on** — [GitHub issues](https://github.com/kschulst/pless/issues).
- **How to work on it** — [CONTRIBUTING.md](../CONTRIBUTING.md).
- **How to use it** — the [documentation site](https://kschulst.github.io/pless/).
