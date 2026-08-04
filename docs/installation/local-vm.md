# Local VM

A disposable virtual machine on your laptop. This is where you should try `pless` first —
every command behaves the same as on real hardware, and a mistake costs you a `vm destroy`
instead of a trip to fetch a keyboard.

## Two backends, two rehearsals

The backend you pick decides which production path you are rehearsing:

| Backend | Guest OS | Provisioning | Mirrors |
|---|---|---|---|
| `lima` | Debian 13 | SSH bootstrap | Raspberry Pi with Raspberry Pi OS |
| `multipass` | Ubuntu 24.04 | cloud-init | Hetzner Cloud |

Both distributions are equally supported and equally tested. If you are heading for a Pi,
use Lima — it exercises the same SSH-bootstrap path and the same Debian package names.

## Install a backend

=== "Lima (Debian)"

    ```bash
    brew install lima
    ```

    Installs as a normal formula, no admin password needed.

=== "Multipass (Ubuntu)"

    ```bash
    brew install --cask multipass
    ```

    Runs a system installer, so it will ask for your password.

## Configure and create

```toml
[target]
type = "vm"

[vm]
backend = "lima"      # or "multipass"
name = "pless-dev"
cpus = 2
memory = "4G"
disk = "20G"
data_size_gb = 5      # size of the simulated data disk
```

```bash
pless doctor
pless vm create
```

First run downloads a disk image, which takes a few minutes. After that, creating a VM is
quick.

## Run the flow

```bash
pless bootstrap                 # skip on multipass — cloud-init already did it
pless storage init --confirm    # use a throwaway passphrase here
pless deploy paperless
pless audit
pless tunnel                    # http://localhost:8000
```

On the Lima backend the VM gets no cloud-init, so `pless bootstrap` does the provisioning —
exactly as it will on your Pi. On Multipass, cloud-init has already applied the same host
spec at boot, mirroring how a cloud server comes up; running `bootstrap` anyway is harmless
and idempotent.

!!! tip "Use an obviously disposable passphrase"

    Something like `dev-vm-throwaway`. Do not reuse the passphrase you intend to use on real
    hardware — a development VM is not a place to practise handling secrets carelessly.

## Where the LUKS volume comes from

There is no second disk in a VM, so `pless` creates a sparse file and attaches it as a loop
device. This is the same code path used on a Pi with `data_mode = "file"`, which is the
default there too — so the rehearsal is faithful, not an approximation.

A loop device does not survive a reboot. `pless` re-attaches it automatically on `unlock` and
`storage status`, so you will not normally notice.

## Things a VM cannot rehearse

Be clear-eyed about the limits. A VM will not tell you about the behaviour of real storage
media, actual power-loss and boot-order behaviour, Pi-specific kernel or PCIe issues, or how
long OCR really takes on the hardware you bought.

For everything else — the commands, the LUKS flow, the compose stack, the firewall, the
audit — it is the real thing.

## Clean up

```bash
pless vm destroy --confirm
```

You will be asked to type the VM's name. It deletes the machine and everything on it.
