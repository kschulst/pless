# Secrets

Everything `pless` needs to keep out of a repository, in one place: what each secret protects,
when it comes into existence, and what happens if you lose it or someone else gets it.

Your password manager is the source of truth. `.env` is a local cache that can be recreated
from it — never the other way round.

!!! danger "Two of these cannot be recovered"

    **The LUKS passphrase** and **`RESTIC_PASSWORD`** have no reset, no recovery e-mail and no
    support desk. Lose the first and the documents are gone. Lose the second and the off-site
    backup is a blob of noise that nobody can open, including you.

    That is not a limitation. It is the property the whole design exists to provide — a stolen
    machine is worthless to whoever took it. The cost is that it is worthless to you too, if
    you are careless with the passphrase.

## What to store, and how

One item per installation, with a hidden custom field per key, works well: name the fields
exactly as the environment variables so copying between the vault and `.env` is mechanical.

Store the two unrecoverable passphrases as **separate items**, not as fields inside a longer
list. They are the ones you will need under stress — after a power cut, or on a new laptop
with nothing else set up — and they are the ones worth protecting against your own vault
being lost:

- Turn on your password manager's emergency access, so someone you trust can reach them.
- Consider a copy on paper, in a sealed envelope, somewhere physically separate from the
  machine. An archive meant to outlive a laptop should outlive a vault too.

Never store them on the machine `pless` manages. Anything written there is available to
whoever ends up holding it.

## Format

Two classes, and which one a secret belongs to follows from who has to type it.

**Machine secrets** are never typed by a person. They are generated, pasted once into a vault,
and read by software from then on. They should be as long as the receiving software tolerates:

```
qX7nP2vK4mR8tL5wY3bN6cF9dH1jS0aZgE4uT7iO2kM
```

43 characters from `A–Z a–z 0–9 - _` — 32 random bytes, base64url, no padding. 256 bits.
`POSTGRES_PASSWORD`, `PAPERLESS_SECRET_KEY` and `PAPERLESS_ADMIN_PASSWORD` use this, and
`pless init --secrets` generates them.

**Human secrets** are typed by a person, from a vault or from paper, sometimes at a console
after a power cut and sometimes on a borrowed machine at the worst moment of the year. They
trade length for transcribability:

```
9K2M4-XR7TQ-B8HNV-5WGDC-3PFJZ
```

25 characters from Crockford's base32 alphabet — the digits and the uppercase letters, minus
`I`, `L`, `O` and `U`, which are the ones people misread as `1`, `1`, `0` and `V`. Grouped in
fives so the eye can keep its place, and case-insensitive so a phone keyboard cannot get it
wrong. 125 bits, which is far past anything that will ever be brute-forced.

The **LUKS passphrase** and **`RESTIC_PASSWORD`** use this form, because they are the two you
will one day read off a sheet of paper with the machine in front of you and no vault to hand.

!!! note "Why not words?"

    Diceware-style word passphrases are easier to memorise and to dictate over the phone, and
    they are a good choice. They are not the default here because neither of these secrets
    needs to be memorised — both are pasted from a vault in normal use — and shipping a
    wordlist adds a data file and a licence to a tool whose value is being small.

    If you prefer words, use them. `pless` accepts any passphrase; only the ones it generates
    for you follow the format above.

Generate one with `pless secrets generate`, which prints a single secret and nothing else, so
it can be piped into a vault CLI without a header getting in the way.

## The inventory

| Secret | Protects | Created by | If you lose it |
|---|---|---|---|
| LUKS passphrase | Every document, at rest | You, at `pless storage init` | **The documents are gone.** No recovery |
| `RESTIC_PASSWORD` | Every off-site snapshot | You, before `pless backup init` | **The backup is unreadable.** No recovery |
| `B2_ACCOUNT_ID` / `B2_ACCOUNT_KEY` | Write access to the backup bucket | Backblaze B2 console | Reissue — carefully, see below |
| `POSTGRES_PASSWORD` | The database, on the encrypted volume | `pless init --secrets` | Reset on the target |
| `PAPERLESS_SECRET_KEY` | Session and token signing | `pless init --secrets` | Reset; everyone signs in again |
| `PAPERLESS_ADMIN_PASSWORD` | The Paperless admin account | `pless init --secrets` | Reset on the target |
| `PAPERLESS_API_TOKEN` | API access, used by import | Paperless UI, after deployment | Revoke and create a new one |
| `TS_AUTHKEY` | Joining the tailnet | Tailscale admin console | Create a new one |
| `HCLOUD_TOKEN` | Your Hetzner project | Hetzner Cloud console | Create a new one |

Only the first two are irreplaceable. Everything else can be reissued by whoever controls the
service it belongs to — which is you.

## When each one comes into existence

Several do not exist when you first run `pless init`, so a checklist written on day one is
wrong by day three. In flow order:

1. `HCLOUD_TOKEN` — only if you let `pless` create the machine.
2. `TS_AUTHKEY` — before `pless tailscale up`.
3. `PAPERLESS_ADMIN_PASSWORD`, `PAPERLESS_SECRET_KEY`, `POSTGRES_PASSWORD` — at
   `pless init --secrets`. Copy them to the vault immediately; `.env` is not a safe place to
   leave the only copy.
4. **LUKS passphrase** — at `pless storage init`. Put it in the vault *before* you type it.
5. `PAPERLESS_API_TOKEN` — after `pless deploy paperless`, from the Paperless web interface.
6. `RESTIC_PASSWORD`, `B2_ACCOUNT_ID`, `B2_ACCOUNT_KEY` — before `pless backup init`.

---

## The LUKS passphrase

**What it protects.** The encrypted volume at `/opt/paperless`, which holds every document,
the database, and the server-side `.env`. When the volume is locked, the machine holds nothing
readable.

**Where it lives.** Your password manager, and nowhere else. `pless` never writes it to disk,
never puts it in `.env`, and never passes it as a command-line argument — it travels on stdin
and goes straight into `cryptsetup`
([ADR 0014](https://github.com/kschulst/pless/blob/main/adr/0014-secrets-never-in-argv.md)).

**If you lose it.** The documents are gone. There is no keyslot recovery, no escrow and no
vendor to ask. This is deliberate
([ADR 0001](https://github.com/kschulst/pless/blob/main/adr/0001-encrypt-data-key-never-on-the-machine.md)).

**If someone else gets it.** They still need the machine or its disk image. Change it as soon
as you can, and treat the archive as read by someone else in the meantime.

**Can it be changed?** Yes, while you still know it — LUKS holds several keyslots, so
`cryptsetup luksChangeKey` over SSH replaces it without re-encrypting anything. There is no
`pless` command for this yet.

**Where it comes from.** `pless storage init --confirm` asks you to choose one.
`pless storage init --confirm --generate` produces one in the format above instead, shows it
once, and refuses to format anything until you confirm you have saved it.

**Prove you have it** with `pless preflight --drill`, which locks and unlocks the volume for
real. Do it while the volume is empty, when being wrong is free.

## `RESTIC_PASSWORD`

**What it protects.** Every off-site snapshot. restic encrypts the repository with it, so the
backup has the same confidentiality property as the volume it came from — including against
Backblaze.

**Where it lives.** Your password manager, and in `/opt/paperless/backup.env` on the target
(mode 0600, root-owned, on the encrypted volume, so it is unreadable whenever the volume is
locked).

**If you lose it.** The repository is an encrypted blob nobody can open. The snapshots are
intact and permanently unreadable, which is the same thing as gone.

**If someone else gets it.** They need the repository too. Anyone with both can read every
document you have ever backed up. Rotate it, and remember that old snapshots stay readable
with the old password.

**Can it be changed?** Yes, while you still know it — `restic key add` and `restic key remove`
change the password without rewriting the data.

**Prove you have it** with `pless backup verify`, which restores from the repository rather
than checking that a file exists.

## `B2_ACCOUNT_ID` and `B2_ACCOUNT_KEY`

**What they protect.** Write access to the bucket holding your snapshots. Not the contents —
those are already encrypted before they leave the machine.

**Where they live.** Your password manager, and `/opt/paperless/backup.env` on the target.

**If you lose them.** Create a new application key in the B2 console. Nothing is lost.

**If someone else gets them.** They can permanently destroy every snapshot in the bucket.

!!! danger "The machine's key can delete the backup, and this is not yet solved"

    restic creates and removes files in `locks/` during an ordinary backup, so the key on the
    machine must have B2's `deleteFiles` capability. That capability authorises
    [`b2_delete_file_version`](https://www.backblaze.com/docs/cloud-storage-file-versions),
    which removes a specific version **permanently** — "as if you never uploaded that
    version". Bucket versioning and a lifecycle rule do not prevent this: they govern
    *automatic* cleanup of hidden and superseded versions, not explicit deletion.

    So whoever holds the machine can enumerate every version and delete it. Until
    [ADR 0017](https://github.com/kschulst/pless/blob/main/adr/0017-tamper-resistance-in-the-bucket.md)
    settles on a mechanism that actually enforces retention — Object Lock is the likely
    answer — **treat the off-site copy as destroyable by anyone who takes the machine**, and
    keep a second copy somewhere the machine has no credentials for.

    Scope the key to a single bucket anyway, with no `writeBuckets`, `deleteBuckets`,
    `writeBucketRetentions`, `writeKeys` or `deleteKeys`. That limits the blast radius to one
    bucket and stops the key reconfiguring the bucket itself. It does not make the data
    undeletable, and this page will not pretend otherwise.

**Reissuing.** Create a replacement in the B2 console scoped the same way — one bucket, no
bucket-settings capabilities. The console offers a full-access key by default.


**The master application key** — the one B2 gives you when you create the account — never goes
on the machine. Keep it in your vault; it is what you use to fix things after a compromise.

## `POSTGRES_PASSWORD`

**What it protects.** The Paperless database, which is reachable only from inside the compose
stack and never published outside loopback.

**Where it lives.** Your password manager, `.env`, and the server-side `.env` on the encrypted
volume.

**If you lose it.** Recoverable: read it from the target's `/opt/paperless/.env`, or set a new
one in Postgres and update both files. Nothing depends on knowing the old value.

**If someone else gets it.** It is useless without a shell on the machine, since the database
does not listen outside the container network. Rotate it anyway if the machine was exposed.

## `PAPERLESS_SECRET_KEY`

**What it protects.** Django's signing of sessions and tokens. Not the documents.

**If you lose it.** Generate a new one. Everyone is signed out and API tokens must be
recreated; nothing is destroyed.

**If someone else gets it.** They can forge sessions if they can also reach the web interface —
which, over Tailscale only, means being on your tailnet
([ADR 0004](https://github.com/kschulst/pless/blob/main/adr/0004-access-through-tailscale-only.md)).
Rotate it and sign in again.

## `PAPERLESS_ADMIN_PASSWORD`

**What it protects.** The Paperless admin account created on first start.

**If you lose it.** Reset it inside the container with Paperless's own
`manage.py changepassword`. The documents are untouched.

**If someone else gets it.** They can read everything through the web interface, if they can
reach it. Change it, and check the audit trail in Paperless.

## `PAPERLESS_API_TOKEN`

**What it protects.** API access, which is how documents are imported
([ADR 0012](https://github.com/kschulst/pless/blob/main/adr/0012-import-through-the-rest-api.md)).

**When it exists.** Only after deployment — you create it in the Paperless web interface, under
your user profile.

**If you lose it, or someone else gets it.** Revoke it there and create a new one. It is the
cheapest secret here to replace.

## `TS_AUTHKEY`

**What it protects.** Joining your tailnet. It decides *which* tailnet the machine joins — one
key belongs to one tailnet, so if you have several, you choose by choosing the key.

**If you lose it.** Create a new one in the Tailscale admin console. Prefer keys that are
single-use and expire; the machine only needs it once.

**If someone else gets it, and it is still valid.** They can put a device on your tailnet.
Revoke it, and remove any device you do not recognise.

## `HCLOUD_TOKEN`

**What it protects.** Your Hetzner Cloud project — creating and deleting servers. Only needed
if you let `pless` create the machine for you; every other way of obtaining one needs nothing
here.

**If you lose it.** Create a new token in the console.

**If someone else gets it.** They can delete the server. The archive survives if your backups
do, which is the entire argument for having them.

## What `pless` does with them

Secrets reach the target on **stdin**, never as command-line arguments — anything in `argv` is
readable by every local user through `ps`, and lands in shell history and process accounting
([ADR 0014](https://github.com/kschulst/pless/blob/main/adr/0014-secrets-never-in-argv.md)).

Files holding secrets on the target are written mode 0600, owned by root, on the encrypted
volume. They are unreadable whenever the volume is locked — which is whenever the machine is
powered off, or in someone else's hands.

`pless` never displays a secret it has stored or received. The one thing it does print is a
freshly generated value from `pless secrets generate` and `pless storage init --generate`,
which exist precisely so you can put that value somewhere safe — it is shown once, at the
moment it comes into existence, and never again. Neither `pless audit --json` nor any record
`pless` writes contains a secret.
