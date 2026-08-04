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

## 2026-08-02: OS-grilling, LAN-trusselmodell og produktmål

| # | Tema | Beslutning | Begrunnelse |
|---|------|-----------|-------------|
| 27 | OS (reviderer #23) | Debian 13 og Ubuntu 24.04+ **likestilt** — begge valideres. Anbefaling ved installasjon på Raspberry Pi: RPi OS (Trixie), fordi Pi Foundations kernel fikser Pi-maskinvarefeil først | Grillingen avdekket at Ubuntu 24.04 har dokumenterte kernel panics og PCIe-enumereringsfeil på Pi 5 + NVMe, fikset i RaspOS-kernel 6.6.y før Ubuntu. Samtidig falt hovedargumentet mot RPi OS bort: Bookworm manglet `docker-compose-v2` i apt, men Trixie (Debian 13, siden okt. 2025) har den under samme navn som Ubuntu — host-spec-en kjører uendret på begge |
| 28 | Debian 13 som minimum | Debian 12 avvises av bootstrap | Bookworm mangler `docker-compose-v2`; å støtte den ville krevd Dockers eget apt-repo og en tredjepart til i tillitskjeden |
| 29 | Dev-backends (utvider #21) | To VM-backends som speiler hver sin produksjonsløype: `lima` → Debian 13 + SSH-bootstrap (som Pi), `multipass` → Ubuntu + cloud-init (som Hetzner) | «Likestill»-kravet krever at begge distroer OG begge provisjoneringsmekanismer faktisk kjøres. Lima har `debian-13`-template og installeres uten sudo |
| 30 | LAN-trusselmodell | Angriper antas å ha tilgang til det lokale nettet (kompromittert wifi, IoT-enhet, gjest). Målet er **null åpne porter** sett fra LAN: Paperless på 127.0.0.1, og SSH kun på `tailscale0` etter `pless harden` | Kenneths krav: nettverkstilgang skal ikke gi tilgang til dokumentene. Revisjon viste at Paperless allerede var tett, men SSH sto åpent mot hele nettet — og Tailscale var aldri implementert |
| 31 | Innstramming er et eget steg | `pless harden --confirm` kjøres ETTER at `pless tailscale up` er verifisert oppe; harden nekter å kjøre hvis Tailscale ikke svarer | Å stenge LAN-SSH uten en verifisert alternativ vei inn er utelåsing. Recovery hvis alt ryker: skjerm og tastatur fysisk på maskinen |
| 32 | `pless audit` | Verifiserer lyttende sockets, UFW-policy, Docker-publiserte porter, SSH-konfig og at data ligger på LUKS. Exit ≠ 0 ved avvik | Særlig for Docker/UFW-fella: Docker skriver egne iptables-regler forbi UFW, så en port publisert uten `127.0.0.1:`-prefiks blir LAN-synlig selv om UFW sier deny. Uten en sjekk som feiler, oppdages slikt aldri |
| 33 | Tailnet-valg | Boksen blir med i ett tailnet, valgt av `TS_AUTHKEY`. `[tailscale] login_server` støtter selvhostet Headscale | Tailscale tillater flere tailnets per konto, men bare ett aktivt per enhet. Nøkkelen er allerede tailnet-spesifikk, så `pless` trenger ingen egen tailnet-parameter |
| 34 | Produktmål | `pless` skal bli et produkt som promoteres på egen nettside og kan installeres av andre. Navnet `pless` er ledig på PyPI (verifisert 2026-08-02) | Hever #20 fra «senere» til førsteklasses mål. Påvirker: lave maskinvarekrav (#22), begge distroer (#27), og web-klargjøring (#35) |
| 35 | Web-klargjøring | Kjernemodulene (`storage`, `deploy`, `bootstrap`, `docscan`, `audit`, `tailscale`) importerer verken `typer` eller `rich` og returnerer dataklasser; `cli.py` er eneste presentasjonslag. Nye kommandoer får `--json`, og alt som spør om hemmeligheter må ha en ikke-interaktiv vei | Et web-grensesnitt skal kunne drive CLI-et under panseret. Seamen fantes allerede — den er nå et krav, ikke en tilfeldighet |

| 36 | Docker-pakker per distro | `hostspec.packages_for()` gaffler: Debian → `docker-cli` + `docker-compose`, Ubuntu → `docker-compose-v2`. Verifisert empirisk, ikke fra dokumentasjon | Nettsøk påsto at `docker-compose-v2` fantes i Debian 13 — det gjør den ikke; Compose v2 heter der `docker-compose` (v2.26.1). Debian skiller dessuten klienten ut i `docker-cli`, som kun er en Recommends, og vi bruker `--no-install-recommends`. Begge feilene ga en tilsynelatende vellykket bootstrap med daemon uten `docker`-kommando |
| 37 | Vent på apt-låsen | Bootstrap venter på `cloud-init status --wait` og setter `DPkg::Lock::Timeout=300` | En fersk maskin kjører cloud-init eller unattended-upgrades ved første boot. Uten dette feiler bootstrap med «Could not get lock» — og det ville truffet Pi-en like hardt som VM-en |

## 2026-08-04: Dokumentasjon, publisering og produktretning

| # | Tema | Beslutning | Begrunnelse |
|---|------|-----------|-------------|
| 38 | Docs-verktøy | Zensical | Material for MkDocs går EOL 5. november 2026, og Zensical er samme teams etterfølger. Det «utbredte» valget er annonsert dødt — å velge det ville betydd migrering innen tre måneder. Python-basert, som resten av prosjektet |
| 39 | Docs-språk | **Engelsk** på nettsiden; `DECISIONS.md` forblir norsk som internt arbeidsdokument | Målgruppen for et selvhostet Paperless-oppsett er global, og hele økosystemet rundt (Paperless, Tailscale, Docker) er engelsk. Tospråklig ble vurdert og forkastet: én av versjonene råtner alltid |
| 40 | Publisering | Offentlig repo `kschulst/pless` fra dag én, med ærlig alfa-merking og en statustabell som skiller det som virker fra det som ikke finnes | Åpenhet om umodenhet koster ingenting; å dokumentere funksjoner som ikke finnes koster tillit. Reponavn = kommandonavn = PyPI-navn |
| 41 | Én kilde per ting | `docs/pi-oppsett.md` absorbert av installasjonskapittelet, README krympet til pitch + lenke | Duplisert dokumentasjon kommer alltid i utakt |
| 42 | Lisens | MIT | Vanligste valget for et CLI-verktøy; lavest friksjon for gjenbruk |
| 43 | Restore-øvelser | `pless backup verify` skal gjøre en EKTE gjenoppretting til et scratch-område og inspisere resultatet — ikke bare bekrefte at en arkivfil finnes. Leveres SAMMEN med backup, ikke etterpå | En backup som aldri er gjenopprettet er en tro, ikke en backup |
| 44 | Web-wizard | Planlagt: web-grensesnitt for oppsett som driver CLI-et / et felles API under panseret. Forsterker #35 — kjernemodulene forblir fri for typer/rich, og alt som spør om hemmeligheter må ha en ikke-interaktiv vei | Senker terskelen for at andre kommer i gang, som er hele poenget med #34 |
| 45 | Åpen kildekode | Målet er et selvstendig opensource-produkt. CI (ruff + pytest) kjører på hver push | Følger av #34 og #40 |

## Åpne punkter

- **CLI-et snakker fortsatt norsk** mens dokumentasjonen er engelsk. Docs viser engelsk konsolloutput som ikke stemmer med virkeligheten. Må oversettes før prosjektet deles bredt — se #39/#40.
- **Neste store steg: installere på Kenneths Pi 5.** Krever at han er til stede (flashing med skjerm/tastatur). Guide: `docs/installation/raspberry-pi.md`.
- Import- og backup-fasene er ikke bygget ennå — arkivet finnes ikke før de er det.
- `TS_AUTHKEY` mangler, så `pless tailscale up` og `pless harden` er ikke live-testet; koden er enhetstestet.
- Konverteringspipeline for `.enex` — venter på resultat av `pless docs scan` mot ekte data.
- Eksakt Pi-modell/RAM bekreftes med `cat /proc/device-tree/model` og `free -h` før bootstrap.
- Varslingskanal (healthchecks.io vs ntfy vs e-post) — mønsteret er dead-man-ping; kanal velges ved implementasjon.
- Tang-server-plassering (router/NAS/annen Pi) — avklares først når auto-opplåsing faktisk prioriteres.
- Hetzner-sporet: CX23-pris verifiseres i konsollen før evt. `server create`; Hetzner auto-backup ble ikke valgt (restic+exporter dekker behovet).
