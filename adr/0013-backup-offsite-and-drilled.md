# 0013 — Back up off-site, at a different provider, and drill the restore

- **Status:** Accepted
- **Date:** 2026-07-17
- **Status note:** decided; the backup feature itself is not built yet

## Context

Home hardware is lost as a unit. Theft, fire, a failed drive and a bad power supply all take
the machine and everything attached to it in one event. A cloud server has a different but
real failure mode: an account suspended, a payment card that expires, a provider dispute.

Meanwhile the archive contains two separable things. The documents themselves usually exist
elsewhere — in the folders they were scanned into. The tags, correspondents, dates and OCR
text exist only here, and represent hours of organising.

## Decision

Two layers. Paperless's own document exporter produces a restorable export, pulled down to
the operator's machine. Encrypted [restic](https://restic.net/) snapshots go to S3-compatible
object storage **at a different provider from wherever the machine runs** — Backblaze B2 by
default.

`pless backup verify` performs a **real restore** into a scratch location and inspects the
result, rather than confirming that an archive file exists.

## Consequences

A backup that shares a blast radius with what it protects is not a backup. Using a different
provider is the whole point of the off-site layer, which is why it is not the same object
storage as the server's host.

restic is already encrypted, so the off-site copy satisfies the same confidentiality property
as the volume it came from. That makes `RESTIC_PASSWORD` a second secret with no recovery
path, which the documentation says plainly.

The exporter's layout keeps original files and metadata separable, so extracting "just the
documents" stays trivial.

Restore drills ship *with* backup, not after. A backup that has never been restored is a
belief. Until this exists, `pless preflight` refuses to give a green light and says the
machine holds the only copy of whatever was imported — silence there would read as approval.

For an archive of a few gigabytes, B2's free tier covers it, so cost is not a reason to skip
this.

## Alternatives considered

- **Object storage at the same provider as the server:** convenient, one invoice, and shares
  the failure that matters most.
- **Snapshots at the hosting provider only:** captures everything including the database, and
  disappears with the account.
- **Verify by checking the archive exists:** the illusion of a backup, which is worse than
  knowing you have none.
