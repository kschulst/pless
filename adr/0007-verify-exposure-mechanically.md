# 0007 — Verify exposure mechanically, because Docker bypasses the firewall

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

Docker manipulates iptables directly. A container port published without an explicit
`127.0.0.1:` prefix becomes reachable from the entire local network **while `ufw status`
continues to report that incoming traffic is denied**. The firewall is not lying; it simply is
not in the path.

The failure is completely silent. Nothing warns, nothing logs, and the service works — which
is the problem. A configuration that was correct on Tuesday can be exposed on Wednesday
because someone added a port mapping.

Configuration drifts. Guarantees that depend on nobody making that mistake are not
guarantees.

## Decision

`pless audit` collects the machine's actual state in one SSH round trip and checks five
things: nothing listens outside loopback; UFW is active with default deny and no LAN-open
rules; no container publishes outside `127.0.0.1`; SSH accepts keys only; and the data really
sits on the LUKS device.

It exits non-zero on findings, so it works in cron and CI as well as by hand. Known ports are
named in the output, because a bare port number does not tell an operator what to fix.

Analysis is pure functions over collected text, so every check is unit-tested without a
machine.

## Consequences

The security properties this project claims are checkable rather than asserted. Running
`pless audit` after any compose change is cheap and catches the Docker trap specifically.

The audit also surfaces things nobody thought about. It found LLMNR listening on every
interface — noticed only because the check reports everything rather than what was expected.

It verifies network exposure and storage encryption, and nothing else. It does not audit
Paperless user accounts, Tailscale ACLs, passphrase strength, or the operator's laptop. The
documentation states those boundaries so a green result is not over-read.

## Alternatives considered

- **Document the Docker trap and trust people to avoid it:** documentation does not fail a
  build.
- **Report findings without failing:** an exit code is what lets cron and CI notice. Silent
  reports get read once.
