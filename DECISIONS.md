# Decision log

Why `pless` is built the way it is. Each entry records a decision and the reasoning
behind it, so that a future contributor can tell a deliberate choice from an accident —
and knows what would have to change for the decision to be revisited.

Decisions are append-only. When one supersedes another, the older entry stays and is
marked.

---

## Architecture

**1. Scope: declarative provisioning, imperative operations.**
Cloud-init or a bootstrap script handles one-time provisioning (hardening, Docker, firewall);
the CLI owns everything that runs repeatedly (deploy, backup, health, unlock). Idempotency
comes free from the declarative half, and the imperative half is where bugs would cost most.

**2. Ingestion through the Paperless REST API, not the consume folder.**
The API returns a task ID per file, so upload status, duplicate rejection and consumption
confirmation come from Paperless itself. A consume-folder approach would mean
re-implementing all three by watching files disappear.

**11. Multi-target from one codebase.**
`target.type` selects Raspberry Pi, local VM or Hetzner Cloud. Everything downstream of
"a host reachable over SSH" is target-agnostic, so adding a provider is roughly a hundred
lines and touches nothing else.

**18. Host spec separated from delivery mechanism.**
One specification, two renderers: cloud-init user-data for targets that support it, and an
idempotent shell script over SSH for those that do not. Constants are shared, so the two
cannot drift apart.

**35. The CLI is a library with a terminal front-end.**
Core modules (`storage`, `deploy`, `bootstrap`, `docscan`, `audit`, `tailscale`) import
neither `typer` nor `rich` and return dataclasses. `cli.py` is the only presentation layer.
This is what lets a web interface drive the same code rather than reimplementing it.

---

## Security

**3. Access through Tailscale only.**
No public ports, no domain required, no login page exposed to the internet. A personal
document archive holds identity documents and financial records; the attack surface should
be zero, not merely small.

**14. LUKS2 with automatic cipher selection.**
AES-XTS where the CPU has AES instructions, Adiantum otherwise. The Raspberry Pi 4's
Broadcom SoC lacks ARM crypto extensions, which would make AES painfully slow; Adiantum is
Google's answer to exactly that problem. Detection reads `/proc/cpuinfo`, so no user
configuration is involved.

**15. Manual unlock after boot.**
The root filesystem is unencrypted; only the data volume is encrypted, and its key never
touches the machine. The consequence is accepted deliberately: after every reboot the
archive stays locked until someone supplies the passphrase. A stolen machine yields
hardware and an OS, not documents.

**30. The local network is treated as hostile.**
The threat model assumes an attacker already has LAN access — a guest device, an unpatched
IoT appliance, a compromised phone. After `pless harden`, nothing listens on the LAN at
all: Paperless binds to localhost, the database publishes no ports, and SSH accepts
connections only over `tailscale0`.

**31. Hardening is a separate, guarded step.**
`pless harden` refuses to run until Tailscale is verified working. Closing LAN SSH without
a confirmed alternative route in is a lockout, and the only recovery is physical access.

**32. Exposure is verified mechanically, not assumed.**
`pless audit` checks listening sockets, firewall policy, published container ports, SSH
configuration and storage encryption, and exits non-zero on findings. It exists chiefly
because **Docker writes its own iptables rules that bypass UFW** — a port published without
a `127.0.0.1:` prefix becomes LAN-visible while the firewall still reports deny. That
failure is completely silent, so only an explicit check catches it.

**Secrets never travel in argv.**
Passphrases and auth keys are passed to the target on stdin. Anything in `argv` is readable
by any local user through `ps`, and often lands in shell history and process accounting.

---

## Platform

**22. Hardware requirements are deliberately low.**
Required: a Raspberry Pi 4 or 5 (or any x86-64 machine), 64-bit, at least 4 GB of RAM, and
any boot medium. Not required: NVMe, a specific HAT, or a particular case. Paperless-ngx 2.x
is not built for 32-bit ARM, which is the one hard exclusion.

**25. Data lives in an encrypted file, not a partition.** *(supersedes an earlier
two-media design)*
Both Ubuntu and Raspberry Pi OS grow the root partition to fill the disk on first boot,
leaving no free space to partition, and ext4 cannot be shrunk while mounted. A fixed-size
LUKS file gives the same isolation a partition would — the archive cannot grow into the
operating system's space — and makes the tool indifferent to the storage medium.

**27. Debian and Ubuntu are equals.**
Both are validated. On Raspberry Pi hardware, Raspberry Pi OS is *recommended*, because the
Raspberry Pi Foundation's kernel receives fixes for Pi-specific hardware first; NVMe and
PCIe bugs on the Pi 5 were fixed there before they reached Ubuntu's kernel.

**28. Debian 13 is the minimum.**
Debian 12 lacks Compose v2 in apt, and supporting it would mean adding Docker's own
repository — another party in the trust chain for no benefit.

**36. Docker package names differ per distribution, and this was found by running it.**
Debian 13 ships Compose v2 as `docker-compose` (there is no `docker-compose-v2`) and splits
the client into `docker-cli`, which is only a *Recommends* — so with
`--no-install-recommends` you get a daemon with no client. Ubuntu is the mirror image.
Documentation claimed otherwise; only execution on real machines revealed it.

**37. Bootstrap waits for the apt lock.**
A freshly installed machine runs cloud-init or unattended-upgrades on first boot. Without
waiting for `cloud-init status --wait` and setting `DPkg::Lock::Timeout`, bootstrap fails
with "Could not get lock" on exactly the machines it is meant to set up.

---

## Operations

**16. Automatic security updates, including reboots.**
`unattended-upgrades` installs patches and reboots at 04:30 when the kernel changes. The
cost is accepted: the archive is locked afterwards until someone unlocks it, roughly once or
twice a month. Patch hygiene wins over convenience.

**19. Off-site backup is the primary disaster layer.**
Encrypted restic snapshots to object storage at a *different provider* from wherever the
machine runs, plus Paperless's own exporter pulled down locally. Home hardware is lost to
theft, fire or media failure as a unit; a backup sharing a blast radius with what it
protects is not a backup.

**43. Restore drills ship with backup, not after it.**
`pless backup verify` will perform a real restore into a scratch location and inspect the
result, rather than confirming that an archive file exists. A backup that has never been
restored is a belief.

---

## Project

**34 / 45. This is an open-source product.**
Documentation, code and user-facing output are in English. Requirements are kept low and
both supported distributions are tested, because the goal is that someone other than the
author can install it. CI runs lint and tests on every push.

**38. Zensical for documentation.**
Material for MkDocs reaches end of life on 5 November 2026, and Zensical is the same team's
successor. Choosing the more widespread tool would have meant migrating within months.

**41. One source per fact.**
Installation instructions live in the documentation site; the README is a pitch and a link.
Duplicated documentation always drifts.

**Documentation drift is caught mechanically.**
`tests/test_docs_consistency.py` fails when a command exists but is undocumented, when the
docs describe a command that no longer exists, when a config section is missing from the
reference, when an internal link is broken, or when a page is missing from the navigation.
A convention decays; a failing build does not.

**42. MIT licence.**

---

## Open questions

- **Import is not built.** Without it there is no archive. Planned through the Paperless
  REST API (see decision 2).
- **Backup is not built.** Planned as decisions 19 and 43 describe.
- **Alternative unlock methods.** Manual `pless unlock` is the only route today. Clevis and
  Tang would let the machine unlock itself at home while staying sealed elsewhere; a
  phone-based approach is also worth exploring. The LUKS volume is created with a spare key
  slot so either can be added without re-encrypting.
- **Password-manager integration.** Writing generated secrets straight into a password
  manager through its CLI, where one is available, would remove the most error-prone manual
  step in setup.
- **Web setup wizard.** A browser-based installer driving the same core modules as the CLI.
  Decision 35 keeps that possible; the interface itself is unbuilt.
- **Raspberry Pi validation.** The Pi path is documented and the code paths are exercised on
  Debian, but no run on real Pi hardware has been completed yet.
- **Hetzner validation.** Code and unit tests exist; no live run against the API.
