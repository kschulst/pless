# SPDD artefacts

How non-trivial changes to `pless` are designed before they are written.

Specification-Prompt-Driven Development is a way of forcing the decisions out into the open
*before* the code exists, rather than making them implicitly while typing. Two artefacts, both
committed before the change they describe:

| Directory | Artefact | Answers |
|---|---|---|
| [`analysis/`](analysis/) | Strategic analysis | What is actually being asked, which domain concepts exist already, which direction is right, and what could go wrong |
| [`prompt/`](prompt/) | REASONS Canvas | Requirements, Entities, Approach, Structure, Operations, Norms, Safeguards — detailed enough to implement from |

## The flow

```bash
/spdd-analysis        # enrich the context: domain, direction, risks
/spdd-reasons-canvas  # the structured prompt
/spdd-generate        # code and tests from the canvas
/spdd-sync            # fold what the code taught you back into the canvas
```

The analysis is where an argument belongs. It is expected to end with open questions, and those
questions are expected to be *settled* — in conversation, against the real documentation, or by
trying the thing — before a canvas is written. An analysis whose open questions are still open
is not ready to become a canvas.

The canvas is where the argument stops. It says what will be built, in enough detail that
implementing it is transcription rather than design.

## Naming

```
NNN-YYYYMMDD-[Action]-kebab-case-description.md
```

`NNN` is a monotonic sequence shared by every artefact describing the same work — the analysis
and the canvas for one change carry the same number in their respective directories. `Action`
is `[Analysis]`, `[Feat]`, `[Fix]`, `[Refactor]`, `[Test]` or `[Docs]`.

**No ticket or project prefix.** Work items live in
[GitHub issues](https://github.com/kschulst/pless/issues), and a filename that embeds an issue
number goes stale the moment work is split, merged or renumbered. Artefacts link to issues in
their content, and each issue gets a comment pointing back at the artefact.

## When to use it

Use it for anything that touches several modules, changes the security model, or would
otherwise be decided implicitly while typing.

Skip it for bug fixes, small refactors and documentation edits. It is a tool for avoiding
implicit decisions, not a ceremony — and a canvas for a two-line fix is a way of looking busy.

## What belongs somewhere else

- **Lasting architectural decisions** — [`adr/`](../adr/README.md). A canvas *references* ADRs
  rather than restating them, and a decision that outlives the change that prompted it should
  be a record rather than a paragraph in a canvas nobody rereads.
- **What is being worked on** — [GitHub issues](https://github.com/kschulst/pless/issues).
- **How to use `pless`** — the [documentation site](https://kschulst.github.io/pless/).
- **How to work on `pless`** — [CONTRIBUTING.md](../CONTRIBUTING.md).

## Are these kept up to date?

The analysis is a snapshot: it says what was known and argued at the time, and it is not
rewritten afterwards. Where a later session settles one of its open questions, the resolution
is appended to it rather than replacing the reasoning that led there — the same discipline the
ADRs use, and for the same reason.

The canvas is updated while its change is being implemented, because the code teaches you
things the design could not. Once the change has landed, the canvas is history too; the
documentation site and the ADRs are what stay true.
