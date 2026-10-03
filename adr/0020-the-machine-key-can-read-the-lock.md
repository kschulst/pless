# 0020 — The machine key can read the lock it depends on

- **Status:** Accepted
- **Date:** 2026-10-03
- **Context references:** issue #16; amends [0017](0017-tamper-resistance-in-the-bucket.md)

## Context

[ADR 0017](0017-tamper-resistance-in-the-bucket.md) makes Object Lock the mechanism that protects
the archive, and gives the machine a key holding exactly `listBuckets`, `listFiles`, `readFiles`,
`writeFiles` and `deleteFiles` — "that single exclusion", the absence of `bypassGovernance`,
"is the entire protection". The same record says `pless audit` verifies that the bucket carries a
default retention period.

Those two cannot both hold. Probed against a real bucket, a key with exactly those five
capabilities gets the field back with its value withheld:

```json
"fileLockConfiguration": { "isClientAuthorizedToRead": false, "value": null }
```

So the check 0017 promises cannot be performed by the machine it is about. Worse, a check written
against that answer has two ways to be wrong and no way to be right: reading `null` as "no
retention" reports CRITICAL on a correctly configured bucket every time, and treating a missing
value as acceptable certifies a destroyable setup by silence — which is exactly what 0017 refused
when it rejected versioning as evidence of anything.

## Decision

The machine's key also holds **`readBucketRetentions`**. Six capabilities, still no
`bypassGovernance`.

The capability is read-only. It permits reading a bucket's lock configuration and nothing else:
it cannot enable Object Lock, cannot disable it — B2 does not allow that at all — and cannot
shorten or remove a retention period, which needs `writeBucketRetentions`.

With it, the check stays on the target, inside `pless audit`, where the operator already looks.

## Consequences

The protection becomes checkable from the machine that depends on it. No second credential, no
separate command, and nothing for the operator to remember to run.

Five states become distinguishable, and the distinctions are the point:

| What the target sees | What it means |
|---|---|
| `isClientAuthorizedToRead: false` | the key was minted without this capability — a fault in the key itself |
| `isFileLockEnabled: false` | no Object Lock. Critical |
| `isFileLockEnabled: true`, `defaultRetention.mode: null` | lock on, nothing retained. Critical, and it looks like success |
| `mode: "compliance"` | protects, and binds the operator too. A finding, not a pass |
| `mode: "governance"` with a period | compare the period against `version_retention_days` |

The third row is the one that justifies this record. It is a real, persistent state — every bucket
looks like that between `b2_create_bucket` and the call that sets retention — and it is what an
operator who enabled Object Lock in the console and stopped there has. Objects written into such a
bucket carry no retention and can be destroyed permanently by the machine's own key. A check that
asks only `isFileLockEnabled`, which is the obvious implementation, calls it protected.

**The period is an object, not a number.** B2 stores
`{"duration": 90, "unit": "days"}`, so the check compares the duration only after reading the
unit. Assuming days would silently pass a bucket configured in another unit, which is the class of
mistake this check exists to catch.

A key minted before this record reports its own gap rather than skipping the check, so existing
installations surface the problem instead of quietly passing.

The cost is one more capability on a machine that may be stolen. It reads a policy; it cannot
change one. Against that, the alternative is a protection nobody can confirm is switched on.

## Alternatives considered

- **Keep five capabilities and move the check to the operator's machine** (`pless b2 check`):
  keeps the key minimal, and costs a command, a credential and a check the operator has to
  remember. It would also report on a bucket from a machine that is not the one at risk.
- **Keep five and check nothing**, which is what the canvas implied: leaves the operator trusting
  that Object Lock is on. 0017 exists because that kind of trust turned out to be misplaced.
- **Prove it by writing a file and trying to delete it:** demonstrates the property rather than
  reading a policy, which is stronger. It also writes an object into the archive that then cannot
  be removed until its retention expires, every time the audit runs.
