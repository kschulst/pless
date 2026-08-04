# Configuration

Two files, with a strict division of labour.

**`pless.toml`** holds everything that is not a secret. Commit it. `pless` finds it by walking
up from your working directory, the way git finds its repository root.

**`.env`** holds secrets. It is git-ignored, and it is a *cache* — your password manager is
the source of truth. If your laptop dies, the password manager is what gets you back in.

## pless.toml

### `[target]`

```toml
[target]
type = "pi"     # "pi" | "vm" | "hetzner"
```

The single value every other command reads.

### `[tailscale]`

```toml
[tailscale]
login_server = ""    # empty = Tailscale's control plane; a URL for self-hosted Headscale
hostname = ""        # empty = use the machine's own hostname
```

Which tailnet the machine joins is decided by `TS_AUTHKEY`, not here — one auth key belongs
to one tailnet. If you have several tailnets, you choose between them by choosing the key.

### `[pi]`

```toml
[pi]
host = ""              # hostname.local, a Tailscale name, or an IP
user = "ubuntu"        # the user you created when flashing
data_mode = "file"     # "file" | "partition"
data_size_gb = 200     # file mode only; sparse, costs nothing until filled
data_device = ""       # partition mode only; use /dev/disk/by-id/...
```

`data_mode = "file"` is the default and the recommendation. Ubuntu and Raspberry Pi OS grow
the root partition to fill the disk on first boot, which leaves no free space to partition —
and a fixed-size encrypted file gives the same isolation a partition would, since the data
cannot grow into the operating system's space.

#### Sizing `data_size_gb` {#sizing}

The file is sparse, so unused space costs nothing on disk. The temptation is therefore to
set it to nearly the whole disk. Resist that.

The file lives on the root filesystem, so a full archive means a full operating system:
Docker stops working, logging fails, and — worst — the ext4 filesystem *inside* the LUKS
volume starts getting I/O errors on write. That is a nastier failure than an ordinary full
disk, because the corruption happens a layer down.

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
collection therefore lands near 17 GB fully built out, and 50 GB gives it three times room
to grow.

**Start low.** Growing the volume is documented and undramatic — `truncate`,
`cryptsetup resize`, `resize2fs`, all while mounted (see
[Grow or move storage](../cookbook/storage.md)). Shrinking requires creating a new volume and
copying everything across. The asymmetry points one way: pick a modest number and raise it
when `pless server df` says so.

With `data_mode = "partition"`, always use a `/dev/disk/by-id/...` path. Names like `/dev/sdb`
are assigned in boot order and will eventually point somewhere you did not intend.

### `[vm]`

```toml
[vm]
backend = "lima"      # "lima" (Debian 13) | "multipass" (Ubuntu)
name = "pless-dev"
cpus = 2
memory = "4G"
disk = "20G"
data_size_gb = 5
```

### `[hetzner]`

```toml
[hetzner]
location = "hel1"
server_type = "cx23"
image = "debian-13"
server_name = "paperless-01"
```

Verify server type and pricing in the Hetzner console before creating anything — both change.

### `[ssh]`

```toml
[ssh]
user = "root"
key_path = "~/.ssh/id_ed25519"
```

`pless` shells out to the system `ssh` rather than using a Python SSH library, so your agent,
`~/.ssh/config`, `known_hosts` and any `ProxyJump` setup all work without being
reimplemented.

### `[paperless]`

```toml
[paperless]
version = "2.20.15"        # exact image tag — bump deliberately
timezone = "Europe/Oslo"
ocr_languages = "nor+eng"  # Tesseract codes, joined with +
admin_user = "admin"
```

The version is pinned on purpose. A floating tag means an unattended pull can carry you
across a database migration at a moment you did not choose.

`ocr_languages` takes [Tesseract language codes](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html):
`nor`, `eng`, `deu`, `swe`. More languages means slower OCR, so list only what you have.

### `[paths]`

```toml
[paths]
local_documents = "./documents"
local_backups = "./backups"
```

Local directories on *your* machine, not the target. `local_documents` is the
default location `pless docs` commands look at; `local_backups` is where downloaded
backups will land once that feature exists.

### `[storage]`

```toml
[storage]
min_free_gb_after_upload = 10
max_disk_usage_percent_after_upload = 70
```

Thresholds for `pless docs estimate`. Deliberately conservative — a full disk corrupts
databases in ways that are tedious to recover from.

### `[access]`

```toml
[access]
mode = "tailscale"   # "tailscale" | "ssh-tunnel"
```

### `[backup]`

```toml
[backup]
restic_repository = ""   # e.g. "b2:my-bucket:paperless"
```

Reserved for the backup feature, which is not built yet.

## .env

Never commit this. `.gitignore` covers it.

```bash
# Hetzner Cloud API token (Read & Write) — hetzner target only
HCLOUD_TOKEN=

# Generated by `pless init --secrets`. Save these in your password manager.
PAPERLESS_ADMIN_PASSWORD=
PAPERLESS_SECRET_KEY=
POSTGRES_PASSWORD=

# Tailscale auth key — decides which tailnet the machine joins
TS_AUTHKEY=

# Paperless API token — created in the Paperless UI after deployment
PAPERLESS_API_TOKEN=

# Reserved for off-site backup (not built yet)
RESTIC_PASSWORD=
B2_ACCOUNT_ID=
B2_ACCOUNT_KEY=
```

Any of these can also come from the environment, which is how a CI system or a future web
interface would supply them without a file on disk.

!!! danger "Two secrets have no recovery path"

    **The LUKS passphrase** — which `pless` never stores anywhere, including here. Lose it and
    the documents are gone.

    **`RESTIC_PASSWORD`**, once backups exist. Lose it and the off-site backup is an
    encrypted blob nobody can open, including you.

    Both belong in your password manager the moment they exist.

## Secrets handling

Secrets are passed to the target on **stdin**, never as command-line arguments. Anything in
`argv` is visible to every local user through `ps`, and often lands in shell history and
process accounting logs.

On the target, the server-side `.env` is written mode 0600, owned by root, on the encrypted
volume. It is therefore unreadable when the volume is locked — which is most of the time the
machine is powered off or in a thief's hands.
