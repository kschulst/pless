# Verify your box is sealed

`pless audit` answers one question: **what can someone already on your network reach?**

The threat model is not a hacker in a distant country. It is a device on your own Wi-Fi — a
guest's laptop, a smart bulb running firmware nobody has patched since 2019, a TV that talks
to servers you have never heard of. "It's only on my home network" is a hope, not a boundary.

```bash
pless audit          # human-readable
pless audit --json   # for scripts and dashboards
```

It exits non-zero when something is wrong, so it drops straight into a cron job or a CI
pipeline. Run it after any change to the compose configuration.

## A sealed box

```console
✓ listening sockets: Nothing listens outside loopback — your LAN sees zero ports.
✓ firewall: UFW active, default deny, no LAN-open rules.
✓ docker ports: No container publishes outside loopback.
✓ ssh: Key-based authentication only.
✓ encrypted storage: Data lives on the LUKS device /dev/mapper/paperless-data.
```

Zero ports. A port scan from another machine on your network finds nothing at all — not a
login page to attack, not an SSH banner to fingerprint.

## The findings, one by one

### Listening sockets

Everything the machine is listening on, excluding loopback. Known ports are named, so you get
`0.0.0.0:22 (SSH)` rather than a number to look up.

The common finding before hardening:

```console
✗ listening sockets: Exposed to LAN: 0.0.0.0:22 (SSH), [::]:22 (SSH).
  Run `pless harden` to bind SSH to tailscale0.
```

That is expected on a fresh install and disappears after `pless harden`. If you see something
else — an unexpected service, a port you do not recognise — investigate before hardening.

### Firewall

Checks that UFW is active, defaults to denying incoming traffic, and has no rules open to
`Anywhere` that are not bound to the Tailscale interface.

A rule reading `22/tcp (OpenSSH) ALLOW IN Anywhere` means every device on your LAN can reach
SSH. After hardening it becomes `22/tcp on tailscale0`, which is a different thing entirely.

### Docker ports — the important one

This check exists because of a trap that catches experienced people.

**Docker writes its own iptables rules, bypassing UFW.** Publish a port without an explicit
`127.0.0.1:` prefix and it becomes reachable from your entire network — while `ufw status`
continues to report, accurately as far as it knows, that incoming traffic is denied. Your
firewall is not lying to you exactly; it simply is not in the path.

```yaml
ports:
  - "127.0.0.1:8000:8000"   # correct — loopback only
  - "8000:8000"             # exposed to the whole LAN, and UFW will not stop it
```

The generated compose file gets this right. The check exists because configuration drifts,
and because the failure is completely silent — nothing warns you, nothing logs it, and the
service simply works, which is the problem.

```console
✗ docker ports: Published outside loopback (bypasses UFW!): web: 0.0.0.0:8000->8000/tcp.
  All ports must be bound with a 127.0.0.1 prefix.
```

Fix it by adding the prefix and redeploying.

### SSH

Confirms password authentication is off. `pless bootstrap` disables it, so a failure here
means something re-enabled it — a manual edit, or a package update dropping in a new default
config.

### Encrypted storage

Confirms `/opt/paperless` is mounted from `/dev/mapper/paperless-data` and not from a plain
partition. Data on an unencrypted disk defeats the entire theft protection.

An unmounted volume is reported as a *warning* rather than a failure, because "locked" is a
safe state — see [After a power loss](power-loss.md).

## What audit does not check

Be clear about the boundaries. It verifies network exposure and storage encryption. It does
not audit Paperless's own user accounts and permissions, your Tailscale ACLs, the strength of
your passphrase, or whether your laptop — which holds the SSH key that opens everything — is
itself secure.

That last one deserves a moment's thought. After hardening, your laptop's SSH key and your
Tailscale identity are the keys to the archive. Protect them accordingly: a passphrase on the
SSH key, disk encryption on the laptop, and device approval enabled in Tailscale.

## Automating it

```bash
0 8 * * * cd /path/to/pless && pless audit || mail -s "pless audit failed" you@example.com
```

Since it exits non-zero on findings, any tool that understands exit codes can watch it.
