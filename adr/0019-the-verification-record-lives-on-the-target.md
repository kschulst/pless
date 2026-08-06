# 0019 — The verification record lives on the target, and names a snapshot

- **Status:** Accepted
- **Date:** 2026-08-06
- **Context references:** issues #2 and #3

## Context

[ADR 0013](0013-backup-offsite-and-drilled.md) makes a drilled restore part of backup, and
`pless preflight` refuses a green light until one has happened. But `preflight` runs in a
different process — usually on a different day — from `pless backup verify`, so the answer to
"has a restore succeeded?" has to outlive the process that established it.

Wherever it is stored, the awkward property is the same: a record written by the target is
trusted exactly as much as the target. If the machine is compromised, `preflight` reports a
reassuring falsehood. Storing it on the operator's machine removes that, and introduces
different problems — it dies with a laptop reinstall, it makes `preflight`'s answer depend on
which machine you run it from, and a future web interface running on the target cannot see it.

## Decision

The record lives on the target, at `/opt/paperless/backups/verification.json`, beside the run
record and the data it describes. It holds the verification level, an ISO-8601 UTC timestamp,
the snapshot id, expected and found document counts, the sample size, the mismatching paths and
a human-readable detail. No secrets, and the repository's *kind* rather than its location.

`pless backup status` cross-checks the recorded snapshot id against `restic snapshots` and
reports a disagreement. `pless preflight` treats a record older than
`[backup] verify_max_age_days` as not verified.

## Consequences

The record is falsifiable, which is what makes storing it on the target acceptable. It names a
snapshot, so anyone holding `RESTIC_PASSWORD` can ask the repository whether that snapshot
exists and when it was written. A forged record either names a snapshot that is not there —
which the cross-check surfaces — or names a real one, in which case the data really is in the
repository.

What a compromised target can still fake is that the restore-and-compare actually happened.
That residual gap is narrow, and it is not closed by moving the file: a machine that can lie
about verifying can also lie about the count it reports at verification time. The documentation
states once, plainly, that the record is a claim rather than a proof.

The record survives a laptop reinstall, and a web interface running on the target reads it with
the same call the CLI uses ([ADR 0009](0009-cli-is-a-library-with-a-terminal-frontend.md)).

An expiring record means `preflight` can go from green back to amber without anything breaking.
That is intended: a restore proven eleven months ago proves little about a repository that has
been written to every day since.

## Alternatives considered

- **On the operator's machine, next to `pless.toml`:** the target cannot forge a green
  `preflight`. It also cannot be read by anything running on the target, it is lost when the
  laptop is reinstalled, and it makes readiness a property of the client rather than of the
  installation.
- **Both, with the target winning on conflict:** turns tampering into a visible event, at the
  cost of two writers and a conflict path to design — more machinery than the snapshot-id
  cross-check justifies today.
- **Recompute at `preflight` time by restoring:** no storage and no trust problem, and it turns
  a readiness check into a multi-gigabyte download that nobody would run.
