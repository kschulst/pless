# Commands

Every command is safe to run twice. If one fails halfway, run it again rather than cleaning
up by hand.

!!! note "The CLI currently speaks Norwegian"

    `pless` was written for its author before it became a product, so its help text and
    output are in Norwegian while this documentation is in English. Translation is on the
    roadmap. The command *names* and flags below are accurate.

## Setup

### `pless init [--secrets]`

Creates `.env` from `.env.example`. With `--secrets`, generates a Paperless admin password,
a Django secret key and a PostgreSQL password.

Copy the generated values into your password manager immediately. `.env` is a local cache;
your password manager is the source of truth.

The generated values are machine secrets — 32 random bytes as base64url, because nobody has to
type them. See [Secrets](secrets.md) for the formats and what each one costs you if it is lost.

### `pless secrets generate [--kind human|machine] [--quiet]`

Prints one secret in the documented format, and nothing else, so it can be piped straight into
a password manager's CLI. A reminder goes to stderr unless you pass `--quiet`.

`--kind human` (the default) produces a hyphenated Crockford base32 passphrase — the format for
the LUKS passphrase and `RESTIC_PASSWORD`, which are unrecoverable and therefore the two you may
one day read off a sheet of paper.

`--kind machine` produces a base64url token for secrets no person types.

```console
$ pless secrets generate
9K2M4-XR7TQ-B8HNV-5WGDC-3PFJZ
• Save it now. pless keeps no copy, and terminal scrollback is not a password manager.
```

### `pless doctor`

Checks your local environment: Python version, `ssh`, `uv`, the config file, your SSH key,
and whatever the active target requires. Exits non-zero if something is missing.

Run this first, and whenever something behaves oddly.

## Provisioning

### `pless bootstrap`

Applies the host spec over SSH: Docker, UFW, fail2ban, SSH hardening, unattended-upgrades,
and disabling LLMNR.

Prints hostname, model, OS, architecture and RAM before changing anything — read that line to
confirm you are on the machine you think you are. Refuses to continue on 32-bit
architectures or unsupported distributions.

Waits for cloud-init and the apt lock, so it is safe on a machine that has just booted for
the first time.

### `pless server status`

State of the active target. Shows VM state and address, cloud server details, or SSH
reachability, depending on the target type.

### `pless server df`

Disk usage on the target's root filesystem.

### `pless ssh`

Interactive SSH session on the active target.

## Storage

### `pless storage init --confirm [--generate]`

Formats the data volume as LUKS2 with ext4 and mounts it at `/opt/paperless`.
**Destructive** — it refuses to overwrite an existing LUKS volume.

Asks for a passphrase twice. The cipher is chosen automatically: AES-XTS where the CPU has
AES instructions, Adiantum otherwise.

With `--generate`, `pless` produces one in the [documented human format](secrets.md#format)
instead, shows it once, and asks you to confirm you have saved it before formatting anything.
Answering no formats nothing.

!!! danger "No recovery"

    Save the passphrase in your password manager before you type it. There is no reset.

### `pless storage status`

Whether the volume is LUKS-formatted, open, and mounted.

### `pless unlock`

Unlocks and mounts the volume, then starts the stack. This is what you run after every
reboot or power cut.

### `pless lock`

Stops the stack, unmounts and closes the volume. Useful before physically moving the
machine.

## Deployment

### `pless deploy paperless`

Writes the compose file, the server-side `.env` (mode 0600, on the encrypted volume) and the
systemd unit, then starts the stack. Requires the volume to be mounted.

First run pulls roughly 2 GB of images and migrates the database.

### `pless deploy status`

Container-by-container state.

### `pless deploy logs [service] [--tail N]`

Logs from the stack. Services: `webserver`, `db`, `broker`, `gotenberg`, `tika`.

### `pless paperless health`

HTTP check against Paperless on the target's localhost.

### `pless tunnel`

SSH tunnel forwarding Paperless to `http://localhost:8000`. Ctrl-C closes it.

## Backup

### `pless backup init`

Writes the backup script, its environment file, the systemd unit and the timer to the target,
initialises the restic repository if it is new, and enables the timer.

Requires `[backup] restic_repository` in `pless.toml` and `RESTIC_PASSWORD` in `.env` — plus
`B2_KEY_ID` and `B2_APPLICATION_KEY` for a `b2:` repository. It names everything that is missing
at once rather than one thing at a time.

The environment file lands on the encrypted volume, mode 0600, owned by root, so it is
unreadable whenever the volume is locked.

!!! danger "No recovery"

    `RESTIC_PASSWORD` is the second secret with no reset. Lose it and the repository is an
    encrypted blob nobody can open, including you. Generate one with
    `pless secrets generate` and save it in your password manager before running this.

### `pless backup run`

Runs a backup now: waits for Paperless's task queue to drain, dumps the database, exports the
documents, and writes both into one restic snapshot.

This invokes the same script the timer invokes, so what you test by hand is what runs unattended.

If the encrypted volume is locked — the normal state after every reboot — it says so and stops,
without failing. A backup that cries wolf after every reboot teaches you to ignore it.

### `pless backup export`

Runs Paperless's document exporter alone, leaving the result in `/opt/paperless/export` on the
target. No snapshot, no database dump.

Useful for inspecting what an export contains. It is not a backup: the export sits on the same
machine as the archive.

### `pless backup verify [--level content]`

Proves the documents come back, by restoring them rather than by checking that an archive file
exists.

Restores a random sample from the newest snapshot to a scratch directory, counts the documents
in the snapshot, and compares that count against what the run which produced it recorded — not
against the live archive, which will legitimately have moved on since. restic verifies content
hashes as it restores, so a sample that comes back is a sample proven intact.

Cost scales with `[backup] verify_sample_size`, not with the size of the archive, which is what
makes it cheap enough to run on a timer. It runs weekly by default; see `verify_schedule`.

The result is written to the target and is what `pless preflight` reads. An empty repository
fails: one that was silently recreated looks exactly like success and contains nothing.

`--level full`, the whole rehearsal into a throwaway VM, is
[not built yet](https://github.com/kschulst/pless/issues/3).

### `pless backup status [--json]`

Repository kind, timer state, snapshot count, the newest snapshot, and how the last run ended.

An empty repository is reported as a failure rather than as "nothing yet", because a repository
that was silently recreated looks exactly like success and contains nothing.

## Access and security

### `pless tailscale up`

Installs Tailscale and joins the tailnet. Which tailnet is decided by `TS_AUTHKEY` — one key
belongs to one tailnet. The key is passed on stdin, never in the command line where `ps`
could read it.

### `pless tailscale status`

Tailscale state on the target.

### `pless harden --confirm`

Moves SSH from the LAN to the Tailscale interface only. After this, nothing on your local
network can reach the machine.

Refuses to run unless Tailscale is confirmed working, so you cannot lock yourself out. If
the tailnet is later lost entirely, recovery is a monitor and keyboard on the machine.

### `pless preflight [--drill]`

Answers one question: **is this installation fit to be trusted with documents?**

Checks that the target is reachable, the encrypted volume is open and mounted, Paperless is
answering, the exposure audit is clean, and that off-site backup exists and has been restored
from at least once. Exits non-zero if anything blocking fails.

With `--drill`, it also locks and unlocks the volume, then waits for the stack to return.
That is the only way to *prove* the passphrase you believe in is the one that works — and it
costs nothing while the volume is empty, which is exactly why it belongs before your first
import rather than after.

!!! warning "Preflight will not pass yet"

    Backup is not implemented, so the recoverability checks fail by design. That is the
    honest answer: this machine currently holds the only copy of whatever you import, so
    keep your originals elsewhere.

### `pless audit [--json]`

Checks network exposure and storage encryption. Exits non-zero on findings, so it works in
cron and CI. See [Verify your box is sealed](../cookbook/verify-security.md).

## Documents

### `pless docs scan <path> [--hashes]`

Recursively classifies a local folder: ready to import, needs conversion (`.enex`, `.html`),
or unsupported. Skips hidden and system files. With `--hashes`, computes SHA-256 and reports
duplicate content.

### `pless docs estimate <path> [--local-only]`

Projects disk usage after import, using deliberately pessimistic growth factors. Without
`--local-only`, compares against the target's actual free space and recommends whether to
proceed, batch the import, or add storage.

## Local VM

### `pless vm create`

Creates the development VM. With the `lima` backend you get Debian 13, provisioned by
`pless bootstrap`. With `multipass` you get Ubuntu, provisioned by cloud-init.

### `pless vm destroy --confirm`

Deletes the VM and everything on it. Asks you to type the VM's name.

## Hetzner

### `pless hetzner check-token`

Verifies `HCLOUD_TOKEN` with read-only API calls and lists visible servers and locations.

## Not built yet

`pless docs upload`, `pless backup export`, `pless backup download`, `pless backup verify`
and `pless update` are planned but do not exist. See [Cookbook](../cookbook/index.md) for
what to do in the meantime.
