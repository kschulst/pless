# Switch target

`pless` treats every destination the same way: something reachable over SSH. Moving between a
Raspberry Pi, a local VM and a cloud server is a configuration change, not a different tool.

```toml
[target]
type = "pi"     # "pi" | "vm" | "hetzner"
```

Every command reads that one value. `pless audit` on a VM runs exactly the checks it runs on
your Pi.

## Rehearse before you touch real hardware

This is the most useful thing a second target gives you. Before running something consequential
on the machine holding your documents, run it on a VM that costs nothing to destroy.

```toml
[target]
type = "vm"

[vm]
backend = "lima"     # Debian 13, SSH bootstrap — like a Pi
```

```bash
pless vm create
pless bootstrap
pless storage init --confirm     # throwaway passphrase
pless deploy paperless
pless audit
```

Try the upgrade, the storage migration, the configuration change. Break it, destroy it, start
over:

```bash
pless vm destroy --confirm
```

Then set `type = "pi"` and do it for real, knowing what the output should look like.

### Match the backend to the destination

The two VM backends rehearse different production paths:

| Backend | Guest | Provisioning | Rehearses |
|---|---|---|---|
| `lima` | Debian 13 | `pless bootstrap` over SSH | Raspberry Pi with Raspberry Pi OS |
| `multipass` | Ubuntu 24.04 | cloud-init at boot | Hetzner Cloud |

Heading for a Pi? Use Lima. It exercises the same bootstrap path *and* the same Debian
package names — which matters more than it sounds, because the Docker packages genuinely
differ between the distributions. Debian ships Compose v2 as `docker-compose` and splits the
client into `docker-cli`; Ubuntu ships it as `docker-compose-v2` with the client included.
`pless` handles both, and the VM is where you find out if that handling ever breaks.

## Run several targets at once

Keep separate configuration files:

```bash
pless.toml          # your Pi
pless.vm.toml       # scratch VM
```

`pless` walks up from your working directory looking for `pless.toml`, the way git finds its
repository root. The simplest way to keep two live is separate directories, each with its
own config and `.env`.

!!! warning "Check which machine you are on"

    `pless bootstrap` prints the hostname, model, OS and RAM before it changes anything, and
    `pless server status` tells you which target is active. Get in the habit of glancing at
    that line before running anything destructive. `storage init` on the wrong target
    formats a volume that had your documents on it.

## Moving an archive between targets

There is no migration command yet — it needs the backup and restore machinery that has not
been built. The manual route works today:

1. Copy the data out, following
   [Copy it out by hand](storage.md#copy-it-out-by-hand).
2. Set up the new target completely: `bootstrap`, `storage init`, `deploy`.
3. Stop the stack there: `pless lock`.
4. Restore the copy into `/opt/paperless` on the new target.
5. `pless unlock`, then verify with `pless paperless health` and a search in the web interface.

**Use the same passphrase on both** if you want the option of moving the encrypted volume
itself rather than its contents.

Keep the old target intact until the new one has proven itself. Documents present, search
working, a few files opened and read. Only then reclaim the hardware.

## Cost, briefly

| Target | Roughly |
|---|---|
| Raspberry Pi you own | €5–10 per year in electricity |
| Hetzner CX23 | €4–6 per month |
| Local VM | Free, and not a place to keep anything |

The VM is a rehearsal space, not a home. It lives on a laptop that sleeps, travels, and gets
reinstalled.
