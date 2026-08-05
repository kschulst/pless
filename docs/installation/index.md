# Installation

Pick where your archive will live. The steps after provisioning are identical everywhere —
only getting an SSH-reachable machine differs.

<div class="grid cards" markdown>

-   :material-raspberry-pi: **[Raspberry Pi](raspberry-pi.md)**

    Hardware you own, in your home. Cheapest to run, and nobody else's terms apply.

-   :material-laptop: **[Local VM](local-vm.md)**

    A throwaway VM on your laptop. The right place to try things before touching real hardware.

-   :material-cloud: **[Hetzner Cloud](hetzner.md)**

    Someone else's hardware, always on. Useful if you would rather not own a box.

</div>

## What every install does

Whatever the target, the same five commands take you from a bare machine to a running,
locked-down archive:

```bash
pless bootstrap              # Docker, firewall, fail2ban, SSH hardening, auto-updates
pless storage init --confirm # LUKS2 volume, formatted and mounted
pless deploy paperless       # the Paperless-ngx stack
pless tailscale up           # join your tailnet
pless harden --confirm       # close SSH to the LAN
```

Then `pless audit` to confirm the result, and `pless tunnel` to open the web interface.

Every one of these is safe to run again. If a step fails halfway — a download times out, the
power goes out — run it again rather than trying to clean up by hand.

## The one ordering rule

`pless harden` closes SSH to everything except your Tailscale network. Run it before
Tailscale actually works and you have locked yourself out of your own machine.

`pless harden` therefore refuses to run unless it can confirm Tailscale is up. That check is
the only thing standing between you and a trip to fetch a keyboard, so do not work around it.

!!! danger "Two things have no recovery path"

    **The LUKS passphrase.** There is no reset, no backdoor, no support line. Lose it and
    your documents are gone — that is precisely what makes a stolen machine worthless to a
    thief. Put it in your password manager *before* you type it into `storage init`.

    **Your only copy of a document.** Until backup ships, treat this archive as a convenient
    index of files you still hold elsewhere, not as the place they live.

## Pointing pless at a machine

Point `[host]` in `pless.toml` at a machine:

```toml
[host]
address = "archive.local"
user = "admin"
```

There is no target type, because there is only one kind of target: something reachable over
SSH. A Raspberry Pi, a VM on your hypervisor, an old laptop, a droplet at any provider — all
the same to the tool. See [Switch target](../cookbook/switch-target.md) for using a local VM
as a rehearsal before touching real hardware.
