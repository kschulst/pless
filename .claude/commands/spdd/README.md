# SPDD artefacts

Structured Prompt-Driven Development artefacts, produced with
[open-spdd](https://github.com/) via the `/spdd-*` commands.

- `analysis/` — enriched context: business need, domain concepts, strategic direction, risks
- `prompt/` — REASONS Canvas structured prompts, committed **before** the code they describe

## Naming

`NNN-YYYYMMDD-[Action]-kebab-description.md`

`NNN` is a monotonic three-digit sequence (next = highest existing + 1). Dates are
day-level; the sequence number is the identifier.

**No ticket prefix.** Work items live in
[GitHub issues](https://github.com/kschulst/pless/issues), and an artefact that embeds an
issue number in its filename goes stale the moment work is split, merged or renumbered.
Artefacts link to issues in their content instead, and each issue gets a comment pointing
back at the artefact.

## When to use it

For non-trivial design work: new commands that touch several modules, changes to the
security model, anything that would otherwise be decided implicitly while typing.

Not for bug fixes, small refactors, or documentation edits.

Lasting architectural decisions belong in [`../adr/`](../adr/README.md); a canvas references
those rather than restating them.
