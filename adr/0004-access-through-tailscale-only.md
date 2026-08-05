# 0004 — Reach the archive through Tailscale, not the public internet

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

The archive has to be usable from a phone in a waiting room, not only from the sofa. The
conventional answer is a domain, a reverse proxy, Let's Encrypt and a login page on the public
internet.

That login page is then permanently exposed to the entire internet, protecting documents that
are ideal material for identity theft. It also drags in DNS, certificate renewal, port
forwarding and a proxy to keep patched.

## Decision

Access goes over a Tailscale tailnet. Paperless binds to `127.0.0.1` on the target; nothing
is published, no ports are forwarded, no domain is required. Which tailnet a machine joins is
decided by `TS_AUTHKEY` — one key belongs to one tailnet — and a self-hosted control plane
(Headscale) is supported through `login_server`.

## Consequences

There is no public attack surface at all. An attacker cannot reach a login page, cannot
fingerprint a version, cannot brute-force anything, because there is nothing listening.

Caddy, Let's Encrypt, DNS and port forwarding all disappear from the design, along with their
failure modes.

Access requires Tailscale on every client device. This is a real dependency on a third party
for connectivity — mitigated by Headscale support for those who would rather not have it, and
by SSH tunnelling as a fallback.

Lose tailnet access entirely and, after [ADR 0006](0006-harden-is-a-guarded-step.md), the only
way back in is a monitor and keyboard.

## Alternatives considered

- **Public HTTPS with Caddy and a domain:** the most convenient to use, and it puts a login
  page for your tax returns on the open internet.
- **SSH tunnel only:** no third-party dependency, but painful daily and impractical from a
  phone. Kept as a fallback mode.
- **A VPN of one's own (WireGuard by hand):** the same thing with more setup and no MagicDNS.
