# 0005 — Treat the local network as hostile

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

Self-hosting guides routinely treat "it's only on my home network" as a security boundary.
It is not. A home network contains guest devices, a television that talks to servers nobody
has audited, and IoT appliances running firmware last patched years ago. Any of them can be
the foothold.

An audit of a working installation found the machine mostly sealed — Paperless bound to
localhost, the database publishing no ports — but SSH open to the entire LAN, and Tailscale
never actually installed despite [ADR 0004](0004-access-through-tailscale-only.md) assuming it.

## Decision

The threat model assumes an attacker already has LAN access. The goal is **zero open ports as
seen from the local network**. After `pless harden`, SSH accepts connections only on
`tailscale0`; Paperless remains on localhost; the database, cache, Tika and Gotenberg publish
no host ports at all.

LLMNR is disabled as part of the host spec: it listens on every interface and is a known
poisoning vector. mDNS is deliberately left alone, because `hostname.local` is how the install
guide tells people to find the machine in the first place.

## Consequences

A port scan from another device on the network finds nothing. There is no service to attack
and no banner to fingerprint.

The last remaining route in is the operator's laptop, holding the SSH key and the Tailscale
identity. That laptop becomes part of the trust boundary, which the security documentation
says explicitly rather than leaving implied.

Hardening cannot be the default at install time, since it would close the door being used to
set the machine up. It is a separate, guarded step — see
[ADR 0006](0006-harden-is-a-guarded-step.md).

## Alternatives considered

- **Trust the LAN, rely on the router:** the assumption this ADR rejects.
- **Restrict SSH to a single LAN address:** IP-based trust on a local network is weak — ARP
  spoofing and DHCP changes both defeat it — and it keeps an attack surface for little gain.
