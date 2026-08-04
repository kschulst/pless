# After a power loss

**Symptom:** Paperless is unreachable. The machine responds to ping, or the light is on, but
the web interface times out.

**Cause, almost always:** the machine rebooted and the encrypted volume is locked. This is
the system working correctly, not failing.

## Fix it

```bash
pless unlock
```

Enter your passphrase. It unlocks the volume, mounts it, and starts the stack. Confirm:

```bash
pless paperless health
```

Roughly thirty seconds pass between unlocking and Paperless answering, while the containers
start and the database comes up. If `health` fails immediately after unlocking, wait and try
again before assuming something is wrong.

## Why this happens

Your documents sit on a LUKS2 volume whose key is not stored on the machine — that is the
entire point. Nothing on the box can unlock itself, so every boot leaves the archive sealed
until a human supplies the passphrase.

Reboots come from three places:

- **Power cuts**, which are rare and obvious.
- **Kernel updates**, which are neither. `unattended-upgrades` installs security patches
  automatically and reboots at 04:30 when the kernel changes. Expect this once or twice a
  month.
- **You**, deliberately.

The second is the one that catches people. You did nothing, the machine rebooted itself
overnight for a good reason, and the archive has been quietly locked since 04:30.

## Confirm that is really the problem

```bash
pless storage status
```

A locked volume looks like this:

```console
/dev/loop0: LUKS ✓  open ✗  mounted at /opt/paperless ✗
```

Formatted, but neither open nor mounted. And the stack will be down entirely:

```bash
pless deploy status   # no containers
```

That is not a coincidence. `paperless.service` declares
`RequiresMountsFor=/opt/paperless`, so systemd refuses to start it against an unmounted
volume. Without that guard, Paperless would start against an empty directory and appear to
work — with no documents in it. Failing loudly is the better outcome.

## Find out sooner

The unpleasant version of this is discovering it at a doctor's office when you need a
document. Two ways to learn earlier:

**Check after you notice a power cut.** Low-tech and effective: if the microwave clock is
blinking, run `pless unlock`.

**Dead-man monitoring.** Point a service like [healthchecks.io](https://healthchecks.io) at a
periodic ping from the target, and have it alert when the pings stop. Because the stack does
not start until you unlock, "pings stopped" and "archive is locked" are the same signal.
`pless` does not configure this for you yet — it is on the roadmap.

## Making it unlock itself

There is a legitimate way to avoid the manual step without giving up the guarantee:
[Clevis and Tang](https://github.com/latchset/tang). A Tang server on another
always-on machine at home hands out the key, so the Pi unlocks itself *at home* and stays
sealed anywhere else. A thief who takes only the Pi gets nothing.

The trade-off is honest: you add a second machine to maintain, and a burglar who takes both
boxes defeats it. `pless` does not implement this yet, but the LUKS volume is created with a
spare key slot so it can be added later without re-encrypting anything.

## If unlocking fails

**Wrong passphrase.** LUKS gives no hints. Retrieve it from your password manager and try
again — there is no rate limit, so a typo costs nothing but time.

**Passphrase genuinely lost.** The data is unrecoverable. This is by design and there is no
workaround. Reflash, run `pless storage init` with a new passphrase, and re-import from your
originals.

**`unlock` errors out.** Check the machine is reachable at all with `pless server status`,
then `pless ssh` and look at `dmesg` for storage errors. A dying SD card presents exactly
like this.
