# 0014 — Pass secrets on stdin, never in argv

- **Status:** Accepted
- **Date:** 2026-07-18

## Context

`pless` sends several secrets to the target: the LUKS passphrase, a Tailscale auth key, and
the server-side environment file containing database and admin credentials.

The obvious way is to interpolate them into the remote command string. Anything in `argv` is
readable by every local user on the machine through `ps`, and often ends up in shell history,
process accounting and audit logs. On a multi-user machine that is an immediate leak; on a
single-user machine it is a leak into logs that outlive the moment.

## Decision

Secrets travel on stdin. `sshexec.run()` takes `input_text`, and the remote side reads it —
`cryptsetup --key-file=-`, `read -r KEY`, `tee` for files. No secret is ever part of a command
string.

Files written on the target go through `tee` from stdin, mode 0600, owned by root, on the
encrypted volume.

## Consequences

Secrets do not appear in `ps`, shell history or process accounting on either machine.

The server-side `.env` sits on the encrypted volume, so it is unreadable whenever the volume
is locked — which is whenever the machine is powered off, or in a thief's hands.

Remote commands become slightly more awkward to write, since they must consume stdin. That is
a small, one-time cost paid in a handful of call sites.

## Alternatives considered

- **Interpolate into the command string:** simplest, and leaks to `ps` and logs.
- **Write a temporary file and reference it:** leaves the secret on disk, and needs cleanup
  that has to survive failures and interruptions.
