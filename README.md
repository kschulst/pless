# pless

CLI for provisjonering og drift av en selvhostet [Paperless-ngx](https://docs.paperless-ngx.com/),
med tilgang kun via Tailscale. Multi-target: **Raspberry Pi (primær)** eller Hetzner Cloud.

Arkitektur og alle veivalg: se [DECISIONS.md](DECISIONS.md).
Sette opp Pi-en fra scratch: se [docs/pi-oppsett.md](docs/pi-oppsett.md).

## Arkitektur (kort)

```
laptop (pless CLI) ──provisjonering──> target (felles host-spec, adapter per target)
        │                              │  pi: Pi 4/5, Ubuntu Server arm64, fersk flash
        │                              │  hetzner: CX23 @ hel1 via hcloud API
        │                              ├─ hardening, UFW(22), fail2ban, Docker, Tailscale
        ├──ssh (dag-2-drift)─────────> ├─ LUKS2-datapartisjon (pi): Adiantum på Pi 4,
        │   pless unlock etter boot    │  AES på Pi 5 — låses opp med `pless unlock`
        └──Paperless REST API────────> ├─ /opt/paperless: compose-stack bundet til
            (over Tailscale)           │  localhost (webserver, postgres, redis,
                                       │  gotenberg, tika)
                                       └─ tailscale serve → HTTPS på tailnet

backup: restic (kryptert) → Backblaze B2 daglig  ← PRIMÆRT katastrofelag
        + document_exporter → ./backups lokalt ukentlig
varsling: dead-man-ping («oppe men låst», «backup uteble»)
```

## Kom i gang

```bash
uv sync                      # installer avhengigheter
uv run pless init            # opprett .env fra .env.example
uv run pless init --secrets  # generer sterke secrets (lagre i Bitwarden!)
# fyll inn HCLOUD_TOKEN i .env (fra console.hetzner.cloud -> Security -> API tokens)
uv run pless doctor          # sjekk at alt lokalt er klart
uv run pless hetzner check-token
uv run pless docs scan ~/Dropbox/dokumenter --hashes
uv run pless docs estimate ~/Dropbox/dokumenter --local-only
```

## Kommandoer

| Kommando | Gjør |
|----------|------|
| `pless init [--secrets]` | Opprett `.env`, generer secrets |
| `pless doctor` | Sjekk lokalt miljø for aktivt target |
| `pless bootstrap` | Applisér host-spec over SSH på en booted maskin (Pi etter Network Install) |
| `pless vm create` / `vm destroy --confirm` | Dev-VM via Multipass, provisjonert med felles cloud-init host-spec |
| `pless storage init --confirm` | LUKS2-formater datadisken (cipher autodetekteres: AES på Pi 5, Adiantum på Pi 4) |
| `pless storage status` | LUKS/mount-status på target |
| `pless unlock` / `pless lock` | Lås opp/lås datadisken (passphrase fra Bitwarden, sendes kun på stdin); unlock starter stacken |
| `pless deploy paperless` | Legg ut compose-stack + systemd-unit og start Paperless |
| `pless deploy status` / `deploy logs [tjeneste]` | Containerstatus og logger |
| `pless paperless health` | HTTP-helsesjekk mot targetets localhost |
| `pless tunnel` | SSH-tunnel: http://localhost:8000 → target (Ctrl-C lukker) |
| `pless server status` / `server df` | Status og diskbruk for aktivt target |
| `pless ssh` | Interaktiv SSH-sesjon mot aktivt target |
| `pless docs scan <mappe> [--hashes]` | Klassifiser filer, finn `.enex`, duplikater |
| `pless docs estimate <mappe> [--local-only]` | Diskestimat; med target oppe: faktisk `df`-sjekk |
| `pless hetzner check-token` | Verifiser Hetzner-token (kun hetzner-target) |

Aktivt target velges i `pless.toml`: `[target] type = "vm" | "pi" | "hetzner"`.

## Utvikling

```bash
uv run pytest        # tester (all ikke-cloud-logikk er ren og testet)
uv run ruff check .  # lint
uv run ruff format . # format
```

## Faseplan

1. ✅ **Fundament**: config, doctor, token-sjekk, dokumentskanner, diskestimator
2. 🔨 **Provisjonering** (vm-target ferdig, venter på Pi i posten): felles cloud-init
   host-spec, `pless vm create`, LUKS med cipher-autodeteksjon, `pless unlock`/`lock`.
   Gjenstår: flash-guide for Pi + `[pi]`-utfylling, systemd-gating ved deploy
3. ✅ **Deploy**: compose-generator (localhost-bundet, versjonspinnet, secrets kun via
   `${}`-interpolasjon fra server-.env på kryptert disk), `deploy paperless/status/logs`,
   `paperless health`, `pless tunnel`, `paperless.service` som aldri kan starte mot låst disk
4. **Import**: API-basert opplasting med manifest, batching, resume, progresjon
5. **Backup + varsling**: restic → B2 (daglig timer), exporter + nedlasting, verify,
   dead-man-ping, restore-dry-run-sjekkliste
6. **Senere**: Dropbox `paperless-inbox/` → `processed/`-sync, Tang-auto-opplåsing,
   Hetzner-target reaktiveres, delbart image/veiviser

## Sikkerhetsnotater

- Ingen secrets i git — `.env` er gitignored, Bitwarden er source of truth.
- Serveren eksponerer kun port 22 (og Tailscale sin UDP-trafikk); Paperless er aldri
  bundet til offentlig IP.
- Destruktive kommandoer (destroy, wipe, db-reset) kommer til å kreve eksplisitt
  `--confirm` + interaktiv bekreftelse med servernavn.
