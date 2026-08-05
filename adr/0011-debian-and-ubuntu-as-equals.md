# 0011 — Support Debian and Ubuntu as equals, with Debian 13 as the floor

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

The original choice was Ubuntu Server, on the reasoning that it matched the development VM.
That argument weakened under examination, and one fact broke it: **Ubuntu 24.04 has documented
kernel panics and PCIe enumeration failures on the Raspberry Pi 5 with NVMe** — precisely the
recommended hardware. The Raspberry Pi Foundation's kernel had the fix before Ubuntu's did.

The main argument *against* Raspberry Pi OS also turned out to be about the wrong Debian
version. Debian 12 lacks Compose v2 in apt, which would have meant adding Docker's own
repository — but Raspberry Pi OS moved to Debian 13 in October 2025, where Compose v2 is
present.

## Decision

Debian 13+ and Ubuntu 24.04+ are supported equally, and both are tested. On Raspberry Pi
hardware, Raspberry Pi OS is *recommended*, because the Foundation's kernel receives
Pi-specific hardware fixes first.

Debian 13 is the minimum. Debian 12 is rejected rather than supported through a third-party
repository.

## Consequences

Two distributions must actually be exercised, which is why there are two VM backends — Lima
for Debian, Multipass for Ubuntu. "Supported" without a test is a claim, not a property.

Running it for real proved documentation wrong twice, and both would have shipped broken:
Debian ships Compose v2 as `docker-compose` (there is no `docker-compose-v2`), and it splits
the client into `docker-cli`, which is only a *Recommends* — so with `--no-install-recommends`
you get a daemon with no client and a bootstrap that looks like it succeeded.

`hostspec.packages_for()` is the only place the distributions diverge, and a test asserts the
package lists differ so the fork cannot be silently collapsed.

The development VM tracks the current Ubuntu LTS (26.04), because testing what is recommended
matters more than testing what happened to be current when the project started. 24.04 remains
supported: it is the floor, not the recommendation.

## Alternatives considered

- **Ubuntu only:** would have put the recommended hardware on the kernel with known NVMe
  failures.
- **Raspberry Pi OS only:** loses the cloud targets, where Debian or Ubuntu is what providers
  actually offer.
- **Support Debian 12 via Docker's apt repository:** another party in the trust chain, for a
  release that is about to be superseded anyway.
