# 0015 — Documentation drift is a build failure

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

The documentation site is how anyone other than the author is expected to install this. It
went out of date almost immediately: it showed English console output while the CLI still
spoke Norwegian, and it described commands with flags the reference did not list.

"Remember to update the docs" is a convention, and conventions decay under time pressure —
which is exactly when documentation matters most.

## Decision

`tests/test_docs_consistency.py` runs in CI and fails when documentation and code disagree:

- a command exists but is undocumented, or is documented but no longer exists;
- a configuration section exists in the model but not in the reference;
- `pless.toml` uses a section the model does not know;
- a relative link between pages does not resolve;
- a page exists but is missing from the navigation, or the navigation points at a missing
  page.

Repository conventions live in `CLAUDE.md` and `CONTRIBUTING.md` for the half a test cannot
check.

## Consequences

Adding a command now requires documenting it, because CI says so. The test caught the
`preflight` command within seconds of it being written.

On its first run it found three real drifts, including that `zensical.toml` was **not valid
TOML** — multi-line inline tables and trailing commas, which Zensical tolerates and every
other parser rejects. That was invisible until something else tried to read the file.

The test cannot check whether prose is still *true*. It verifies that documented things exist
and existing things are documented; it cannot know that a command's output changed. When
behaviour changes, the pages quoting it still have to be reread, and both `CLAUDE.md` and
`CONTRIBUTING.md` say so.

## Alternatives considered

- **A pull request checklist:** unenforced, and this project mostly does not have pull
  requests yet.
- **Generate the reference from the CLI:** removes drift for command names, but produces
  reference material with no explanation of when or why to use anything.
