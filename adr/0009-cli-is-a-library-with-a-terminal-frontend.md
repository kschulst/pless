# 0009 — The CLI is a library with a terminal front-end

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

A browser-based setup wizard is a stated goal: the terminal is a real barrier for people who
would otherwise self-host their own documents. A wizard that reimplements provisioning, or
that shells out and scrapes human-readable output, would drift from the CLI within weeks.

## Decision

Core modules — `storage`, `deploy`, `bootstrap`, `docscan`, `diskcheck`, `audit`,
`tailscale`, `preflight`, `scaffold` — import neither `typer` nor `rich`, and return
dataclasses rather than printing. `cli.py` is the only presentation layer.

Anything that asks for a secret must also have a non-interactive path, and machine-readable
output is available where a caller would need it (`pless audit --json`).

## Consequences

A web interface can import the same modules and drive the same code, rather than parsing
console output or duplicating logic. Whatever the wizard does, the CLI does identically,
because it is the same function call.

Testing is easier as a side effect: the analysis in `audit` and `preflight` is pure functions
over collected text, so both are covered without a machine.

The discipline has to be maintained. A `rich` import in a core module for one convenient
progress bar would quietly undo it, so this ADR exists to make that a visible violation
rather than a small convenience.

## Alternatives considered

- **A wizard that shells out to `pless` and parses stdout:** couples a UI to human-readable
  text, which is then no longer free to change.
- **A separate implementation for the web:** two codebases making the same security promises,
  one of which will be wrong.
