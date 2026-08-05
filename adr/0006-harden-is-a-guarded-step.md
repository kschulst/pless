# 0006 — Hardening is a separate step that refuses to lock you out

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

Closing SSH to the LAN ([ADR 0005](0005-the-local-network-is-hostile.md)) removes the route
that was used to set the machine up. If Tailscale is not actually working when that happens,
the operator is locked out of their own machine and the only recovery is physical access — a
monitor and keyboard, which for a headless box in a cupboard is a genuinely bad evening.

The failure is easy to reach by accident: `tailscale up` can appear to succeed while the
backend is still authenticating.

## Decision

`pless harden` is a separate command, requires `--confirm`, and **refuses to run unless it can
confirm Tailscale is up** — backend state `Running` with at least one tailnet address.

The firewall changes are ordered so the new rule is added before the old one is removed.

## Consequences

The obvious sequencing mistake becomes impossible. An operator who runs `harden` too early
gets a refusal explaining what to do, rather than losing access.

Hardening is a deliberate act rather than something that happens silently during setup, so
operators know their access model changed.

The guard is the only thing between an operator and a physical recovery trip, so it must not
be weakened for convenience. Documentation says this plainly rather than presenting `harden`
as routine.

## Alternatives considered

- **Harden automatically during bootstrap:** would close SSH before Tailscale exists.
  Guaranteed lockout.
- **Harden without checking:** relies on the operator having verified, which is precisely the
  thing people skip when a command looks like it should just work.
