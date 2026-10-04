# Maintenance

Day-to-day operation of a running archive. None of this is frequent — a healthy install
mostly wants to be left alone — but knowing where to look saves an evening when something
does go wrong.

## The daily reality: it locks itself

This is the single behaviour that surprises people, so it goes first.

Your documents are on an encrypted volume whose key is not stored on the machine. Every
reboot leaves the archive locked and the stack stopped. Reboots happen more often than you
might expect, because security updates are installed automatically and kernel updates reboot
the machine at 04:30.

```bash
pless unlock   # asks for the passphrase, mounts the volume, starts the stack
```

Expect to do this once or twice a month, plus after every power cut. It is the cost of the
guarantee that a stolen machine is worthless. See
[After a power loss](cookbook/power-loss.md) for how to notice it has happened before you
need a document.

## Checking on it

```bash
pless server status      # is the machine up?
pless paperless health   # is Paperless answering?
pless deploy status      # container-by-container state
pless storage status     # LUKS: formatted, open, mounted?
pless server df          # disk usage
```

`pless deploy status` is the one that usually answers the question. A healthy stack shows
five containers up, with `paperless-webserver-1` marked `(healthy)`.

## Are the backups still happening?

```bash
pless backup status      # the last run, and whether a restore has been proven
```

This is the check worth doing weekly, and it answers two different questions. Whether last night's
snapshot was written, and whether anyone has ever restored one — because the second is the only
thing that makes the first worth anything.

```console
$ pless backup status
Repository: b2
Timer: active (daily at 02:00)
Snapshots: 47
Latest: 9f2a1c04 at 2026-10-04 02:13:41
✓ Last run 2026-10-04T02:13:41Z: 2431 documents, snapshot 9f2a1c04.
✓ Verified 2026-10-01T03:00:12Z: Restored 5 of 5 sampled files.
```

Three states are not failures and should not read as one:

- **Skipped — the volume is locked.** The normal state after a reboot. Run `pless unlock`.
- **Skipped — the queue is busy.** The normal state during an import, which keeps Paperless
  consuming for days. It becomes a failure after `[backup] max_busy_skips` runs, by which point an
  import is no longer a likely explanation.
- **An expired verification.** A restore proved two weeks ago says little about a repository
  written to every night since, so it stops counting — `[backup] verify_max_age_days` sets the
  window. Run `pless backup verify`.

A real failure names what went wrong. `journalctl -u pless-backup.service` on the target has the
rest, and `pless deploy logs webserver` has the OCR pipeline's side of it.

## Logs

```bash
pless deploy logs                    # everything, last 50 lines
pless deploy logs webserver          # just Paperless
pless deploy logs db --tail 200      # the database, more history
```

Services are `webserver`, `db`, `broker`, `gotenberg` and `tika`. When a document fails to
import, `webserver` is where the OCR pipeline explains itself.

## Updates

The operating system updates itself. `unattended-upgrades` installs security patches as they
appear and reboots at 04:30 when a kernel update requires it — which is why the archive is
sometimes locked in the morning.

Paperless itself is pinned to an exact version in `pless.toml`:

```toml
[paperless]
version = "2.20.15"
```

Updating is deliberate: edit the version, then redeploy.

```bash
pless deploy paperless
```

The pin is not bureaucracy. A floating tag means an unattended `docker compose pull` can move
you across a database migration you did not plan for, at a time you did not choose. Read the
[Paperless-ngx release notes](https://github.com/paperless-ngx/paperless-ngx/releases) before
bumping a major version.

!!! warning "Back up first — once backups exist"

    Right now `pless` cannot take a backup, which means an upgrade that goes wrong has no
    undo. Until that ships, take a manual snapshot of `/opt/paperless` before major version
    bumps, or accept the risk knowingly.

## Security checks

```bash
pless audit
```

Run it after any change to the compose configuration, and occasionally for its own sake. It
exits non-zero when something is wrong, so it works in a cron job or CI pipeline as well as
by hand.

It checks five things: that nothing listens outside loopback, that the firewall is active
with a default-deny policy and no LAN-open rules, that no container publishes a port outside
`127.0.0.1`, that SSH accepts keys only, and that the data really is on the LUKS device.

The third check earns its place. Docker writes its own iptables rules, *bypassing UFW*. Add a
port mapping without a `127.0.0.1:` prefix and it becomes visible to your entire local
network while UFW cheerfully reports that everything is denied. See
[Verify your box is sealed](cookbook/verify-security.md).

## Disk space

```bash
pless server df
pless docs estimate ~/Documents --local-only
```

Paperless keeps the original file *and* an OCR'd archive copy, so plan for roughly double
your source size. Add exports on top of that. `pless docs estimate` projects the growth using
deliberately pessimistic factors and tells you whether to proceed, import in smaller batches,
or add storage.

## Getting a shell

```bash
pless ssh              # interactive session on the target
pless tunnel           # forward Paperless to http://localhost:8000
```

## When something is properly broken

Work down this list:

1. `pless storage status` — if the volume is locked, that is your answer. `pless unlock`.
2. `pless deploy status` — which container is unhappy?
3. `pless deploy logs <service>` — what does it say?
4. `pless server df` — a full disk breaks things in confusing ways.
5. `pless audit` — has something drifted?

The stack cannot start against a locked volume: `paperless.service` declares
`RequiresMountsFor=/opt/paperless`, so systemd refuses. That is deliberate. It means you will
never see Paperless start up cheerfully with an empty database because the encrypted volume
was not mounted.
