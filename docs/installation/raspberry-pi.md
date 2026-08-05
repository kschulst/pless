# Raspberry Pi

From an unopened box to a running archive. Assumes you have read
[Hardware](../getting-started/hardware.md) and have a Pi 4 or 5 with at least 4 GB of RAM.

## 1. Check the assembly

Skip this if you are booting from microSD or USB.

Unplug the power before touching anything. With an NVMe drive on a HAT or in a case, confirm
that the drive is fully seated and screwed down, that the flat PCIe ribbon is straight and
completely inserted at **both** ends, and that the drive is a genuine M.2 **M-key** NVMe —
M.2 SATA will not work — of size 2280 or smaller.

Many HATs and cases limit PCIe power to around 5 W. A power-hungry SSD can cause instability
that looks like anything but a power problem.

## 2. Write the operating system

=== "With an SD card or USB drive"

    Plug it into your laptop, run [Raspberry Pi Imager](https://www.raspberrypi.com/software/),
    and skip ahead to [Customisation](#customisation).

=== "NVMe, no SD card (Network Install)"

    A Pi 5 can download the Imager over the network and write straight to NVMe, so you never
    need an SD card at all.

    Connect in this order: monitor via micro-HDMI, USB keyboard, **wired** Ethernet, and power
    last. Wi-Fi will not do — Network Install needs a cable.

    Hold **Shift** as the Pi powers on and keep holding until the Network Install screen
    appears.

In Imager, choose:

| Field | Value |
|---|---|
| Device | Your Pi model |
| Operating system | **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)** |
| Storage | The medium you will boot from — check this carefully |

Confirm the choice says **64-bit**. A 32-bit image gives you a Pi that cannot run
Paperless-ngx at all. Ubuntu Server 24.04 or 26.04 (64-bit) is equally supported if you
prefer it; see [Hardware](../getting-started/hardware.md#operating-system) for the trade-off.

### Customisation

Open Imager's customisation settings before writing:

| Field | Value |
|---|---|
| Hostname | Anything you like — `paperless`, `archive`, `box` |
| Username | Anything you like; it must match `[host] user` in `pless.toml` |
| **SSH** | Enabled, **public-key authentication only** |
| Public key | The contents of `~/.ssh/id_ed25519.pub` |
| Locale | Your timezone and keyboard layout |

!!! warning "Paste your public key, don't settle for a password"

    `pless bootstrap` disables password authentication. If you set only a password, you will
    be locked out of your own machine the moment provisioning finishes.

    ```bash
    cat ~/.ssh/id_ed25519.pub   # copy this into Imager
    ```

Write, then power down, disconnect monitor and keyboard, and boot. A Pi 5 boots from NVMe
without an SD card. If it does not, set the boot order explicitly:

```bash
sudo rpi-eeprom-config --edit   # BOOT_ORDER=0xf416   (6 = NVMe)
```

## 3. Find it and configure pless

```bash
ssh <username>@<hostname>.local
```

If mDNS does not resolve, find the address in your router's DHCP list. Then, in `pless.toml`:

```toml
[host]
address = "<hostname>.local"   # or the IP address
user = "<username>"

[storage]
data_mode = "file"
data_size_gb = 100             # sparse — costs nothing until filled
```

Set `data_size_gb` well below your medium's capacity — see
[sizing](../reference/configuration.md#sizing). On a 32 GB card, use about 16.

## 4. Provision

```bash
pless doctor      # should be all green
pless bootstrap   # Docker, UFW, fail2ban, SSH hardening, unattended-upgrades
```

`pless bootstrap` prints the hostname, model, OS, architecture and RAM before changing
anything. Read that line: confirm it is the machine you think it is, and that it says
`aarch64`.

Bootstrap waits for cloud-init and the apt lock before installing, so it is safe to run on a
machine that just booted for the first time.

## 5. Encrypt the storage

```bash
pless storage init --confirm
```

You will be asked for a passphrase, twice.

!!! danger "Put the passphrase in your password manager first"

    There is no recovery. None. A forgotten passphrase means the documents are gone — which
    is exactly why a stolen Pi is worthless to whoever took it. Save it *before* you type it.

The cipher is chosen automatically: AES-XTS where the CPU supports it (Pi 5), Adiantum
otherwise (Pi 4).

## 6. Deploy Paperless

```bash
pless deploy paperless
```

First run pulls roughly 2 GB of container images and then migrates the database, so give it
ten minutes. When it finishes, five containers are running and the web server answers on the
target's localhost — and nowhere else.

## 7. Private access, then close the LAN

Create an auth key in the Tailscale admin console under **Settings → Keys**, put it in `.env`
as `TS_AUTHKEY`, then:

```bash
pless tailscale up        # join the tailnet
pless harden --confirm    # SSH now only via tailscale0
pless audit               # verify
```

A clean `pless audit` looks like this:

```console
✓ listening sockets: Nothing listens outside loopback — your LAN sees zero ports.
✓ firewall: UFW active, default deny, no LAN-open rules.
✓ docker ports: No container publishes outside loopback.
✓ ssh: Key-based authentication only.
✓ encrypted storage: Data lives on the LUKS device /dev/mapper/paperless-data.
```

If Tailscale ever fails completely, you get back in with a monitor and keyboard attached to
the Pi. That is the intended recovery path, and the reason `harden` insists on verifying
Tailscale first.

## 8. Open it

```bash
pless tunnel   # then browse to http://localhost:8000
```

Log in as the admin user from `pless.toml` with `PAPERLESS_ADMIN_PASSWORD` from `.env`.

## After a reboot

The archive stays locked until you unlock it — including after a kernel update reboots the
machine on its own:

```bash
pless unlock   # unlocks the volume and starts the stack
```

See [After a power loss](../cookbook/power-loss.md) for what this looks like in practice and
how to notice it has happened.
