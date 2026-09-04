# Configuration

Two files, with a strict division of labour.

**`pless.toml`** holds everything that is not a secret. Commit it. `pless` finds it by walking
up from your working directory, the way git finds its repository root.

**`.env`** holds secrets. It is git-ignored, and it is a *cache* — your password manager is
the source of truth. If your laptop dies, the password manager is what gets you back in.

## `[host]` — the machine

`pless` configures and operates one thing: a machine reachable over SSH. There is no target
type, because there is only one kind of target. A Raspberry Pi, a VM on your hypervisor, an
old laptop in a cupboard, a droplet at any provider — all the same to the tool.

Say it directly:

```toml
[host]
address = "archive.local"   # hostname, Tailscale name, or IP
user = "admin"
key_path = "~/.ssh/id_ed25519"
port = 22
```

Or point at an SSH config entry instead:

```toml
[host]
ssh_config = "~/.ssh/config"
ssh_alias = "archive"
```

The second form is worth knowing about. Whatever your `ssh_config` describes, `pless` inherits
for free — a bastion via `ProxyJump`, a non-standard port, agent forwarding, connection
multiplexing. If `ssh archive` works in your terminal, `pless` works against it.

It is also what `pless vm create` writes, because a local VM's port changes every time it
restarts. Pointing at Lima's own config file means the address is looked up rather than
remembered, so a restart cannot leave your configuration stale.

Both `ssh_config` and `ssh_alias` must be set for that form to apply; otherwise the direct
fields are used.

### Examples

=== "Raspberry Pi"

    ```toml
    [host]
    address = "archive.local"
    user = "pi"
    ```

=== "VM on your own hypervisor"

    ```toml
    [host]
    address = "192.168.1.40"
    user = "debian"
    ```

=== "Any cloud provider"

    ```toml
    [host]
    address = "ec2-13-51-0-0.eu-north-1.compute.amazonaws.com"
    user = "ubuntu"
    key_path = "~/.ssh/my-provider-key.pem"
    ```

=== "Local VM"

    ```toml
    [host]
    ssh_config = "~/.lima/pless-dev/ssh.config"
    ssh_alias = "lima-pless-dev"
    ```

    Written for you by `pless vm create`.

## `[storage]` — the encrypted volume

```toml
[storage]
data_mode = "file"     # "file" | "partition"
data_size_gb = 100     # file mode only; sparse, costs nothing until filled
data_device = ""       # partition mode only; use /dev/disk/by-id/...

min_free_gb_after_upload = 10
max_disk_usage_percent_after_upload = 70
```

`data_mode = "file"` is the default and the recommendation. Ubuntu and Raspberry Pi OS grow
the root partition to fill the disk on first boot, which leaves no free space to partition —
and a fixed-size encrypted file gives the same isolation a partition would, since the data
cannot grow into the operating system's space.

With `data_mode = "partition"`, always use a `/dev/disk/by-id/...` path. Names like `/dev/sdb`
are assigned in boot order and will eventually point somewhere you did not intend — which, for
a setting that decides what gets formatted, is a very bad day.

The last two are thresholds for `pless docs estimate`, deliberately conservative: a full disk
corrupts databases in ways that are tedious to recover from.

### Sizing `data_size_gb` {#sizing}

The file is sparse, so unused space costs nothing on disk. The temptation is therefore to set
it to nearly the whole disk. Resist that.

The file lives on the root filesystem, so a full archive means a full operating system: Docker
stops working, logging fails, and — worst — the ext4 filesystem *inside* the LUKS volume starts
getting I/O errors on write. That is a nastier failure than an ordinary full disk, because the
corruption happens a layer down.

The rule is that **even a completely full archive must leave the OS room to breathe**:

```
data_size_gb  ≤  disk size − 15 GB (OS and container images) − 20 GB margin
```

| Disk | Sensible ceiling | Reasonable starting point |
|---|---|---|
| 128 GB | ~90 | 50 |
| 256 GB | ~220 | 100 |
| 512 GB | ~470 | 100–200 |
| 1 TB | ~965 | 200 |
| 32 GB microSD | ~16 | 16 |

To size it for your own collection rather than your disk: Paperless keeps the original *and*
an OCR'd archive copy, so budget about 2.2×; local exports add roughly another 1.2×. A 5 GB
collection therefore lands near 17 GB fully built out, and 50 GB gives it three times room to
grow.

**Start low.** Growing the volume is documented and undramatic — `truncate`,
`cryptsetup resize`, `resize2fs`, all while mounted (see
[Grow or move storage](../cookbook/storage.md)). Shrinking requires creating a new volume and
copying everything across. The asymmetry points one way.

## `[tailscale]`

```toml
[tailscale]
login_server = ""    # empty = Tailscale's control plane; a URL for self-hosted Headscale
hostname = ""        # empty = use the machine's own hostname
```

Which tailnet the machine joins is decided by `TS_AUTHKEY`, not here — one auth key belongs to
one tailnet. If you have several tailnets, you choose between them by choosing the key.

## `[access]`

```toml
[access]
mode = "tailscale"   # "tailscale" | "ssh-tunnel"
```

## `[paperless]`

```toml
[paperless]
version = "2.20.15"        # exact image tag — bump deliberately
timezone = "Europe/Oslo"
ocr_languages = "nor+eng"  # Tesseract codes, joined with +
admin_user = "admin"
```

The version is pinned on purpose. A floating tag means an unattended pull can carry you across
a database migration at a moment you did not choose.

`ocr_languages` takes [Tesseract language codes](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html):
`nor`, `eng`, `deu`, `swe`. More languages means slower OCR, so list only what you have.

## `[paths]`

```toml
[paths]
local_documents = "./documents"
local_backups = "./backups"
```

Directories on *your* machine, not the target.

## `[backup]`

```toml
[backup]
restic_repository = ""            # empty disables backup

schedule = "daily"                # systemd OnCalendar for the backup timer
verify_schedule = "weekly"        # how often a restore is verified automatically
verify_sample_size = 20           # files hashed per content verification
verify_max_age_days = 14          # older than this and preflight says "not verified"

quiescence_timeout_seconds = 900  # how long to wait for the task queue to drain

retention_daily = 7
retention_weekly = 8
retention_monthly = 12
retention_yearly = 3

version_retention_days = 90       # reserved; see the warning below

exporter_delete = false           # pass --delete to document_exporter
```

`restic_repository` is passed to restic unchanged. Anything restic understands works:

| Value | Kind |
|---|---|
| `/mnt/backup/restic` | A local directory |
| `b2:<bucket>:paperless` | Backblaze B2 — the off-site layer |
| `s3:s3.example.com/<bucket>` | Any S3-compatible storage |
| `rclone:dropbox:paperless` | Anything rclone can reach |

`pless` deliberately has no provider abstraction, so a backend it has never heard of works as
long as restic supports it.

!!! warning "A local repository is for testing"

    A repository on the same disk protects against accidental deletion and corruption. It is no
    protection at all against the machine being lost, stolen or burnt — which is what backup
    exists for. It is a first-class option because it makes the whole flow, including the
    restore drill, testable without a cloud account.

**Schedules** are systemd `OnCalendar` expressions — `daily`, `weekly`, `Mon *-*-* 03:00:00`.
The timer is `Persistent=true`, so a machine that was asleep at the scheduled time backs up when
it wakes.

**Retention** is applied by `pless backup forget`, which is a manual, deliberate act and never
runs on a timer. The four `retention_*` keys map to restic's `--keep-daily`, `--keep-weekly`,
`--keep-monthly` and `--keep-yearly`.

**`version_retention_days`** is what the bucket's Object Lock default retention is expected to
be. `pless` does not set it — a key that could would be a key that could remove it — but
`pless audit` checks the bucket against this number.

!!! warning "The bucket and the key must be made through the API"

    Object Lock is what stops a compromised machine destroying your snapshots, and neither half
    of it can be configured correctly from the B2 web console:

    - The create-bucket dialog offers Object Lock as **"Compliance mode only"**. Compliance
      cannot be lifted by anyone, including you, until it expires — Backblaze's remedy for a
      period set too long is closing your account. Governance is what you want, and it is
      settable only through `b2_update_bucket`.
    - A console key with "Read and Write" comes back with **all 29 capabilities**, including
      `bypassGovernance`, which defeats Object Lock entirely.

    See [Secrets](secrets.md) for the capabilities a machine key must and must not have, and
    [ADR 0017](https://github.com/kschulst/pless/blob/main/adr/0017-tamper-resistance-in-the-bucket.md)
    for the evidence behind it.

!!! note "Use the S3 endpoint, not `b2:`"

    For Backblaze, set `restic_repository` to
    `s3:https://s3.<region>.backblazeb2.com/<bucket>`. A delete through the S3 API becomes a
    delete marker, which Object Lock permits, so restic works normally. The native `b2:`
    backend deletes file versions outright, which a lock refuses.

**`exporter_delete`** is off by default: `pless` does not delete things you did not ask it to
delete. Turning it on keeps the export directory from growing, at the cost of a partially failed
run having already removed files.

The secrets this section needs — `RESTIC_PASSWORD`, `B2_KEY_ID` and `B2_APPLICATION_KEY` — are
documented in [Secrets](secrets.md).

## Optional: getting a machine

These sections are not kinds of host. They are ways to obtain one, and you can ignore both if
you already have a machine.

### `[vm]`

```toml
[vm]
backend = "lima"      # "lima" = Debian + bootstrap; "multipass" = Ubuntu + cloud-init
name = "pless-dev"
cpus = 2
memory = "4G"
disk = "20G"
```

`pless vm create` builds the VM and writes `[host]` to point at it.

### `[hetzner]`

```toml
[hetzner]
location = "hel1"
server_type = "cx23"
image = "debian-13"
server_name = "paperless-01"
```

Only needed if you want `pless` to create a Hetzner server for you. Every other provider works
too — create the machine however you like, then point `[host]` at it. There is nothing to
implement per provider, because after the machine exists they are all just hosts.

## .env

Never commit this. `.gitignore` covers it.

```bash
# Hetzner Cloud API token — only if you let pless create the server
HCLOUD_TOKEN=

# Generated by `pless init --secrets`. Save these in your password manager.
PAPERLESS_ADMIN_PASSWORD=
PAPERLESS_SECRET_KEY=
POSTGRES_PASSWORD=

# Tailscale auth key — decides which tailnet the machine joins
TS_AUTHKEY=

# Paperless API token — created in the Paperless UI after deployment
PAPERLESS_API_TOKEN=

# Off-site backup — see `pless backup init`
RESTIC_PASSWORD=
B2_KEY_ID=
B2_APPLICATION_KEY=
```

Any of these can also come from the environment, which is how a CI system or a future web
interface would supply them without a file on disk.

!!! danger "Two secrets have no recovery path"

    **The LUKS passphrase** — which `pless` never stores anywhere, including here. Lose it and
    the documents are gone.

    **`RESTIC_PASSWORD`**. Lose it and the off-site backup is an encrypted blob nobody can
    open, including you.

    Both belong in your password manager the moment they exist.

[**Secrets**](secrets.md) documents every one of these in full: what it protects, when it comes
into existence, what happens if you lose it, and what happens if someone else gets it. Read it
before you set up your password manager — the LUKS passphrase is not in the list above, by
design, and it is the one that matters most.

## Secrets handling

Secrets are passed to the target on **stdin**, never as command-line arguments. Anything in
`argv` is visible to every local user through `ps`, and often lands in shell history and
process accounting logs.

On the target, the server-side `.env` is written mode 0600, owned by root, on the encrypted
volume. It is therefore unreadable when the volume is locked — which is most of the time the
machine is powered off, or in a thief's hands.
