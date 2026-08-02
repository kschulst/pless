# Beslutningslogg

Fra grilling-sesjon 2026-07-17. Endres kun med ny, eksplisitt beslutning.

| # | Tema | Beslutning | Begrunnelse |
|---|------|-----------|-------------|
| 1 | Scope | Hybrid: cloud-init eier engangsprovisjonering (hardening, Docker, UFW, fail2ban, Tailscale); `pless` eier alt som kjøres gjentatte ganger | Målet er arkivet, ikke CLI-et. Minst kode der bugs koster mest; idempotens gratis via deklarativ cloud-init |
| 2 | Ingestion | Paperless REST API (`POST /api/documents/post_document/`) over Tailscale for lokal opplasting | Task-ID per fil gir upload-status, duplikatavvisning og konsumpsjonsbekreftelse gratis — erstatter hjemmelaget manifest/resume-logikk |
| 3 | Eksponering | Kun Tailscale. Paperless på localhost, `tailscale serve` gir HTTPS på tailnet. Ingen Caddy, ingen åpne 80/443 | Vitnemål/fakturaer er identitetstyveri-gull; login-side skal ikke stå på internett. Mobiltilgang via Tailscale-appen |
| 4 | Server | CX23 (2 vCPU/4 GB/40 GB NVMe), hel1, Ubuntu 24.04 | Datavolum < 5 GB → ~20 GB fullt utbygd inkl. eksportkopi. Verifisert mot Hetzner juli 2026; pris sjekkes i console ved opprettelse |
| 5 | Dropbox | Fase 2. Modell: `paperless-inbox/` i Dropbox → rclone på server → consume → flytt til `paperless-inbox/processed/` etter vellykket ingestion | Naiv rclone-speiling re-laster evig fordi Paperless sletter konsumerte filer; tilstand må være eksplisitt, og synlig tilstand i Dropbox er ryddigst |
| 6 | Backup | To lag: (a) `document_exporter` → `/opt/paperless/backups` → lastes ned lokalt; (b) restic → Backblaze B2, kryptert | B2 = annen leverandør enn Hetzner → overlever «kontoen låses». Exporterens layout (originalfiler + `manifest.json`) gjør «bare dokumentene» trivielt å skille ut |
| 7 | Secrets | Bitwarden er source of truth; `.env` er lokal cache som kan gjenskapes. Restic-passphrase MÅ i Bitwarden | Overlever tap av både laptop og server |
| 8 | Evernote | Blandet/ukjent format. `pless docs scan` klassifiserer; `.enex`/`.html` flagges som «trenger konvertering» — konverteringssteg scopes etter første skann | Paperless konsumerer ikke `.enex`; vi vet ikke ennå hvor mye som faktisk er ENEX |
| 9 | SSH-bibliotek | subprocess + system-`ssh`, ikke paramiko/fabric | Gjenbruker agent, `~/.ssh/config`, known_hosts, ProxyJump uten reimplementering. Kjedelig og pålitelig |
| 10 | Rekkefølge | V1: server + Paperless + engangsimport + backup. Fase 2: Dropbox-sync. | Brukbart arkiv tidligst mulig; sync skal ikke blokkere |

## Pivot 2026-07-17: Raspberry Pi som primærtarget

Kenneth pivoterte samme dag: billigst mulig drift, egen maskinvare. Ny grilling.
Beslutning #1–3 og #5–10 står; #4 (Hetzner-server) demoteres til sekundærtarget.

| # | Tema | Beslutning | Begrunnelse |
|---|------|-----------|-------------|
| 11 | Target-modell | Multi-target i samme repo: `target = "pi" \| "hetzner"`. Pi er primær; Hetzner beholdes som opsjon (bl.a. for deling med folk uten Pi) | Fase 1 er allerede target-agnostisk (API-ingestion, subprocess-SSH); kun provisjonering er target-spesifikk |
| 12 | Maskinvare | Raspberry Pi 5 (bestilles, 4 GB minimum) er primær; Pi 4 (4/8 GB) skal også støttes (eneste reelle forskjell: LUKS-cipher, se #14). Dedikert til Paperless, fersk flash | 4 GB kjører full stack inkl. Tika/Gotenberg. Pi 5 har ARM crypto extensions (AES i full fart) og 2–3× raskere OCR. Paperless-ngx ≥ 2.0 er arm64-only — Pi ≤ 2 er dødt løp, Pi 3 (1 GB) frarådet |
| 13 | Lagringsmedium | SD-kort til OS, USB-minnepinne til LUKS-datapartisjonen — separate medier. Migrering til SSD (ligger klar) via UUID-mount når pinnen dør | Separasjon: OS-korrupsjon tar ikke data og omvendt. Designet ANTAR at pinnen dør: mount via `/dev/mapper/paperless-data`, aldri devicenavn |
| 14 | Kryptering | LUKS2 på datapartisjonen. Cipher autodetekteres: Adiantum på Pi ≤ 4 (Broadcom mangler ARM crypto extensions), AES-XTS på Pi 5/x86. Tang-klart ekstra keyslot fra dag én | Tyverikrav: stjålet Pi/medie skal være uleselig. Adiantum er Googles cipher for CPU-er uten AES-instruksjoner |
| 15 | Opplåsing | `pless unlock`: ukryptert rot-OS, passphrase (fra Bitwarden) tastes over SSH etter boot, systemd-gating hindrer Docker-start mot umontert disk. Tang/clevis-auto-opplåsing er designklar fase-senere | Ingen initramfs-kompleksitet; ærlig trusselmodell (tyv får OS + tailscale-identitet som revokeres, null dokumenter). Nøkkel kan aldri bo på enheten |
| 16 | Oppdatering | Full auto inkl. reboot (unattended-upgrades). Konsekvens akseptert: arkivet står låst etter kernel-reboots til `pless unlock` kjøres — estimert 1–3 ganger/mnd | Patch-hygiene vinner. Blir låsingene irriterende, flyttes Tang (#15) frem |
| 17 | Varsling | Dead-man-varsling (healthchecks.io-mønster, konfigurerbart): «Pi oppe men låst» og «backup ikke kjørt» | Uten varsling oppdages strømbrudd/låsing først når man trenger et dokument |
| 18 | Provisjonering | Deklarativ host-spec adskilt fra mekanisme: cloud-init for targets som støtter det (Hetzner, Ubuntu Server på Pi), SSH-bootstrap som fallback. Default Pi-OS: Ubuntu Server 24.04 arm64 | Kenneths pushback: ikke lås targets til samme mekanisme. Spec-en er felles, adapterne per target |
| 19 | Backup-rolle | Restic → B2 blir PRIMÆRT katastrofelag (daglig, systemd-timer); exporter + lokal nedlasting ukentlig. B2 gratis-tier (10 GB) dekker < 5 GB-arkivet | Hjemmemaskinvare: tyveri/brann/mediedød tar gjerne Pi + disk samtidig. Restic er allerede kryptert → skybackup oppfyller tyverikravet |
| 20 | Delbart image | Fase-senere, bygges PÅ multi-target-grunnlaget: først repo + guidet `pless init`-veiviser; ekte flashbart image (pi-gen) kun som bevisst beslutning siden det gjør oss til distro-vedlikeholdere | «Del ut til andre»-ambisjonen skal ikke koste arkitektur nå |
| 21 | Dev-target | `target = "vm"`: lokal Ubuntu Server-VM via Multipass (arm64 på Apple Silicon — samme arkitektur og images som Pi-en), provisjonert med samme cloud-init-template. Hele løypa unntatt fysisk maskinvare testes her mens Pi-en er i posten | Multipass har innebygd cloud-init-støtte og validerer #18 direkte; `vm` blir også permanent test-target |

## Maskinvare ankommet 2026-08-01: Pi 5 8 GB + NVMe

Kenneth kjøpte Pi 5 8 GB med Argon NEO 5-kabinett og NVMe-disk — ingen SD-kort.
Det endrer #12/#13 (som forutsatte SD + separat minnepinne).

| # | Tema | Beslutning | Begrunnelse |
|---|------|-----------|-------------|
| 22 | Maskinvare (erstatter #12) | **Krav:** Pi 4 eller 5, arm64, ≥ 4 GB RAM, ett vilkårlig boot-medium. **Ikke krav:** NVMe, HAT eller spesifikt kabinett — takket være #25 er mediumtypen likegyldig for verktøyet. Kenneths oppsett: Pi 5 8 GB, Argon NEO 5, NVMe, uten SD-kort | Pi 5 har ARM crypto extensions → AES-XTS i full fart (verifisert av autodetekten i #14); Pi 4 faller tilbake på Adiantum. NVMe/SSD anbefales fremfor microSD fordi Postgres sliter ut kort, men er ikke påkrevd. Vifter på Pi 5s 4-pins-header styres av firmware, så leverandørscript (RPi-OS-only) trengs ikke. Minimum-kravene holdes bevisst lave for delbarhet (#20) |
| 23 | OS | Ubuntu Server 24.04 LTS arm64, IKKE Raspberry Pi OS Lite | Identisk OS, arkitektur og container-images som dev-VM-en hele stacken er E2E-testet på. RPi OS ville krevd revalidering uten gevinst |
| 24 | Flash-metode | Network Install (Shift under oppstart) skriver Ubuntu Server rett til NVMe. Ingen SD-kort, ingen demontering, ingen rpiboot. Krever skjerm + USB-tastatur + kablet nett | Kenneth har utstyret. Alternativene (M.2-USB-adapter, rpiboot) krever henholdsvis å åpne kabinettet eller nytt verktøy |
| 25 | Datalagring (erstatter #13) | LUKS-fil (sparse, default 200 GB) på rotfilsystemet via loop-device — samme kodevei som vm-target. `data_mode = "partition"` finnes for egen blokk-enhet. Bieffekt: verktøyet blir uavhengig av lagringsmedium (NVMe/USB/microSD), noe som senker terskelen for #20 | Ubuntu auto-grower root til hele disken ved første boot, så det finnes ingen ledig plass å partisjonere, og ext4 kan ikke krympes montert. En fast-størrelse LUKS-fil gir samme isolasjon som en partisjon: dataene kan ikke spise OS-partisjonen. #13s to-medier-prinsipp faller uansett bort med én disk — og en USB-pinne er MINDRE pålitelig enn NVMe, så «beskyttelse» den veien var feil retning. Restic→B2 (#19) er fortsatt primært katastrofelag |
| 26 | Bootstrap-mekanisme | `pless bootstrap` applikerer host-spec over SSH (`sudo sh -s`, script på stdin). Cloud-init-veien beholdes for vm/hetzner | Network Install booter Pi-en før vi noensinne ser boot-partisjonen. #18 forutså nøyaktig dette: felles spec, adapter per target. Samme konstanter (PACKAGES, SSHD_HARDENING, auto-reboot) genererer begge |

## Åpne punkter

- Konverteringspipeline for `.enex` — venter på resultat av `pless docs scan` mot ekte data.
- Eksakt Pi-modell/RAM bekreftes med `cat /proc/device-tree/model` og `free -h` før bootstrap.
- Varslingskanal (healthchecks.io vs ntfy vs e-post) — mønsteret er dead-man-ping; kanal velges ved implementasjon.
- Tang-server-plassering (router/NAS/annen Pi) — avklares først når auto-opplåsing faktisk prioriteres.
- Hetzner-sporet: CX23-pris verifiseres i konsollen før evt. `server create`; Hetzner auto-backup ble ikke valgt (restic+exporter dekker behovet).
