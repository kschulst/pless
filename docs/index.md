# pless

**A searchable archive of your documents, on hardware you own.**

[Paperless-ngx](https://docs.paperless-ngx.com/) is a document management system for the
paper that accumulates in a life: invoices, receipts, contracts, diplomas, letters from
institutions that still send letters. You feed it scans and PDFs; it runs OCR over them so
the text inside becomes searchable, then sorts them by correspondent, document type and date
— guessing sensibly, and learning from your corrections. What you get back is a filing
cabinet you can grep, reachable from a browser or a phone.

It is open source and self-hosted, which is the point: your tax returns and your children's
birth certificates stay on hardware you control rather than in someone else's product
roadmap.

`pless` sets that up and operates it — on a Raspberry Pi in your home, a cloud server, or a
local VM — with two properties most self-hosting guides skip:

- **Encrypted at rest.** Your documents live on a LUKS2-encrypted volume. The key is never
  stored on the machine. Someone who walks off with your Pi gets hardware and an operating
  system, not your tax returns.
- **Invisible on your network.** After hardening, nothing listens on your LAN. Paperless is
  bound to localhost, the database publishes no ports at all, and SSH accepts connections
  only over your Tailscale network. A compromised device on your Wi-Fi finds nothing to
  attack.

!!! warning "Alpha — read this before you rely on it"

    `pless` is under active development and **cannot yet import documents or take backups**.
    Those are the next two milestones. What works today is documented here; what doesn't is
    listed in [Status](#status). Don't put your only copy of anything on it yet.

## What it looks like

```console
$ pless doctor
✓ Python 3.12.3 (requires 3.12+)
✓ ssh found in PATH
✓ pless.toml found
✓ SSH key found (~/.ssh/id_ed25519)

Active target: pi
✓ [pi] host is set in pless.toml
✓ [pi] data_mode=file (200 GB LUKS file)

All clear. Next: pless bootstrap
```

```console
$ pless audit
✓ listening sockets: Nothing listens outside loopback — your LAN sees zero ports.
✓ firewall: UFW active, default deny, no LAN-open rules.
✓ docker ports: No container publishes outside loopback.
✓ ssh: Key-based authentication only.
✓ encrypted storage: Data lives on the LUKS device /dev/mapper/paperless-data.
```

## The whole stack

Everything below the dashed line runs on your machine. Nothing in it is reachable from your
local network once hardening is applied.

```mermaid
flowchart TB
    subgraph laptop["Your laptop"]
        cli["<b>pless</b><br/><small>Python 3.12 · Typer · Rich<br/>Pydantic · httpx</small>"]
        docs["Your documents<br/><small>PDF · images · Office</small>"]
    end

    subgraph tailnet["Tailscale tailnet — WireGuard"]
        ts["MagicDNS + ACLs"]
    end

    subgraph host["Target: Raspberry Pi · Hetzner · local VM"]
        subgraph os["Debian 13 or Ubuntu 24.04 · arm64 or x86_64"]
            sec["<b>Hardening</b><br/><small>UFW · fail2ban · OpenSSH keys only<br/>unattended-upgrades</small>"]
            luks["<b>LUKS2 volume</b><br/><small>AES-XTS or Adiantum · ext4<br/>key never stored on device</small>"]
            subgraph compose["Docker Compose — bound to 127.0.0.1"]
                web["paperless-ngx<br/><small>Django · OCRmyPDF · Tesseract</small>"]
                pg[("PostgreSQL 16")]
                redis[("Redis 7")]
                tika["Apache Tika"]
                got["Gotenberg"]
            end
        end
    end

    subgraph offsite["Off-site — planned"]
        restic["restic<br/><small>encrypted · incremental</small>"]
        b2[("Backblaze B2")]
    end

    cli -- "SSH · provisioning and day-2 ops" --> host
    docs -. "REST API upload (planned)" .-> web
    cli --- ts
    ts --- host
    web --- pg
    web --- redis
    web --- tika
    web --- got
    compose --- luks
    luks -. "nightly snapshot (planned)" .-> restic
    restic --> b2

    classDef planned stroke-dasharray: 5 5
    class offsite,restic,b2 planned
```

## Why not just run Docker Compose yourself?

You can, and many people do. `pless` exists because the interesting parts are not the
`docker compose up` — they are everything around it: making the disk encrypted without
storing the key on the box, making sure a reboot doesn't silently start the stack against
an unlocked volume, keeping the firewall honest when Docker writes its own iptables rules
behind your back, and having a single command that tells you whether any of that has
quietly stopped being true.

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch: **[Getting started](getting-started/index.md)**

    What you need, what it costs, and a ten-minute overview of the whole flow.

-   :material-download: **[Installation](installation/index.md)**

    Step by step, from an unboxed Raspberry Pi to a running archive.

-   :material-wrench: **[Maintenance](maintenance.md)**

    Day-to-day operation: updates, health checks, logs, disk usage.

-   :material-book-open-variant: **[Cookbook](cookbook/index.md)**

    Recipes for the situations you will actually run into.

</div>

## Status

`pless` is verified end to end on Debian 13 and Ubuntu 24.04. Here is the honest state of
each piece:

| Capability | Status |
|---|---|
| Provisioning (Docker, firewall, fail2ban, auto-updates) | :material-check: Working |
| Encrypted storage with LUKS2 | :material-check: Working |
| Deploying the Paperless-ngx stack | :material-check: Working |
| Tailscale access and LAN hardening | :material-check: Working |
| Exposure auditing | :material-check: Working |
| Readiness gate (`pless preflight`) | :material-check: Working |
| Raspberry Pi as a target | :material-progress-clock: Documented, [awaiting hardware validation](https://github.com/kschulst/pless/issues/4) |
| Hetzner Cloud as a target | :material-progress-clock: Code and unit tests, [not validated live](https://github.com/kschulst/pless/issues/5) |
| **Importing your documents** | :material-close: [Not built](https://github.com/kschulst/pless/issues/1) |
| **Backup, restore, and restore drills** | :material-close: [Not built](https://github.com/kschulst/pless/issues/2) |
| Alternative unlock methods | :material-close: [Planned](https://github.com/kschulst/pless/issues/6) |
| Password-manager integration | :material-close: [Planned](https://github.com/kschulst/pless/issues/7) |
| Web setup wizard | :material-close: [Planned](https://github.com/kschulst/pless/issues/8) |

### What comes next

**Import** is the immediate priority — without it there is no archive. **Backup** follows,
and restore drills ship with it rather than after it, because a backup that has never been
restored is a belief rather than a backup. Until both exist, `pless preflight` deliberately
refuses to give a green light.

What is being worked on, and the reasoning behind each piece, lives in the
[issue tracker](https://github.com/kschulst/pless/issues). This page says what works today;
the tracker says what is being done about the rest.
