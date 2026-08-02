# Pi-oppsett fra scratch

Fra ubrukt Raspberry Pi til kjørende Paperless-ngx.

Bakgrunn for valgene: se [DECISIONS.md](../DECISIONS.md) #22–26.

## Krav

**Påkrevd:**

- Raspberry Pi 4 eller 5 — **arm64 er absolutt krav** (Paperless-ngx 2.x finnes
  ikke for 32-bit ARM). Minst 4 GB RAM for full stack med Tika/Gotenberg.
- Et lagringsmedium å boote fra: NVMe, USB-SSD eller microSD. `pless` legger
  dataene i en LUKS-**fil** på rotfilsystemet, så mediumtypen er likegyldig for
  verktøyet.
- Strømforsyning (Pi 5: offisiell 27 W anbefales) og nettverk.

**Ikke påkrevd:** NVMe, spesifikt kabinett, eller SD-kort. Kombinasjonen under er
bare det denne guiden ble skrevet på.

**Anbefalt, ikke påkrevd:** NVMe eller SSD fremfor microSD. Postgres skriver mye,
og microSD-kort dør stille under den lasten — arkivet overlever, men bare fordi
restic→B2 (#19) tar det. Kjører du på microSD, sett `data_size_gb` deretter
(f.eks. 16 på et 32 GB-kort) og ta backup på alvor fra dag én.

Denne guiden er skrevet på: Pi 5 8 GB, Argon NEO 5-kabinett, NVMe, uten SD-kort.

## 1. Sjekk monteringen (strømløs)

Hopp over dette hvis du booter fra microSD eller USB.

Trekk strømmen før du rører noe. Med NVMe via HAT eller kabinett, kontroller at:

- NVMe-en sitter helt inn og er skrudd fast.
- Den flate PCIe-kabelen sitter rett og helt inne i **begge** kontakter.
- Disken er en ekte **M.2 NVMe med M-key** (M.2 SATA fungerer ikke), maks 2280.

Mange HAT-er og kabinetter (bl.a. Argon NEO 5) har ~5 W effektgrense på
PCIe-tilkoblingen — strømkrevende SSD-er kan gi ustabilitet.

## 2. Skriv Ubuntu Server til lagringsmediet

Har du et SD-kort eller en USB-disk du kan koble til en annen maskin, er det
enkleste å bare kjøre Raspberry Pi Imager der og hoppe til «Tilpasninger» under.

**Uten SD-kort — skriv rett til NVMe med Network Install:** koble til i denne
rekkefølgen: skjerm (micro-HDMI), USB-tastatur, ethernet, **strøm til slutt**.

Hold inne **Shift** mens Pi-en får strøm, og fortsett å holde til
Network Install-skjermen kommer. Pi-en laster da ned Raspberry Pi Imager over
nettverket (krever kablet nett — wifi holder ikke).

I Imager velger du:

| Felt | Verdi |
|------|-------|
| Device | Din Pi-modell |
| Operating System | **Other general-purpose OS → Ubuntu → Ubuntu Server 24.04 LTS (64-bit)** |
| Storage | Mediet du skal boote fra (kontroller nøye at det er riktig enhet) |

**Ikke** velg Raspberry Pi OS. Ubuntu Server 24.04 arm64 er nøyaktig samme OS,
arkitektur og container-images som hele stacken er testet mot i dev-VM-en —
Raspberry Pi OS ville betydd revalidering av alt uten gevinst. Sjekk at valget
er **64-bit**; 32-bit gir en Pi som ikke kan kjøre Paperless-ngx i det hele tatt.

Bruker du et kabinett med aktiv vifte: på Pi 5 styres vifter på 4-pins-headeren
av firmware direkte (gjelder bl.a. Argon NEO 5), så leverandørens kontrollscript
— som gjerne kun støtter Raspberry Pi OS — trengs ikke.

### Tilpasninger i Imager

| Felt | Verdi |
|------|-------|
| Hostname | **fritt valg** — `paperless`, `arkiv-01`, `boks`, hva du vil |
| Username | fritt valg — må matche `[pi] user` i `pless.toml` |
| **SSH** | Aktivert — **«Allow public-key authentication only»** |
| Public key | Innholdet i `~/.ssh/id_ed25519.pub` |
| Timezone | Europe/Oslo |
| Keyboard | no |

Lim inn den offentlige nøkkelen din — ikke nøy deg med passord. `pless bootstrap`
slår av passord-innlogging, så uten nøkkel på plass låser du deg ute.

Vertsnavn og brukernavn er dine å velge; `pless` har ingen forventninger om noen
av delene. Noter hva du valgte — begge skal inn i `pless.toml` i neste steg.

Etter skriving: slå av, koble fra skjerm/tastatur, og start Pi-en. Pi 5 booter fra
NVMe uten SD-kort. Booter den ikke, sett boot-rekkefølgen eksplisitt med
`sudo rpi-eeprom-config --edit` og `BOOT_ORDER=0xf416` (6 = NVMe).

## 3. Finn Pi-en og koble til

```bash
ssh <brukernavn>@<vertsnavn>.local
```

Virker ikke mDNS, finn IP-en i ruteren din. Sett så i `pless.toml` — bruk
vertsnavnet og brukernavnet du faktisk valgte i Imager:

```toml
[target]
type = "pi"

[pi]
host = "<vertsnavn>.local"  # eller IP-adressen
user = "<brukernavn>"
data_mode = "file"
data_size_gb = 200          # sparse — tar ikke plass før den fylles.
                            # Sett lavere enn mediets størrelse: ~16 på et 32 GB-kort.
```

## 4. Kjør pless

```bash
pless doctor                      # skal bli helgrønn med target=pi
pless bootstrap                   # Docker, UFW, fail2ban, SSH-hardening, auto-oppdatering
pless storage init --confirm      # LUKS2 (AES-XTS på Pi 5), ext4, montert på /opt/paperless
pless deploy paperless            # hele Paperless-stacken
pless tunnel                      # http://localhost:8000
```

`pless bootstrap` skriver ut vertsnavn, modell, OS, arkitektur og RAM før den
endrer noe — verifiser at vertsnavnet er maskinen du tror, og at det står
`aarch64`.

**Lagre LUKS-passphrasen i Bitwarden før du taster den inn.** Uten den er disken
med vilje uleselig — også for deg.

## 5. Etter strømbrudd eller kernel-reboot

Arkivet står låst til du låser opp:

```bash
pless unlock      # låser opp OG starter stacken
```

Dette er tyverisikringen som virker: stjeles Pi-en, er NVMe-innholdet uleselig,
og `paperless.service` kan ikke starte mot en låst disk.
