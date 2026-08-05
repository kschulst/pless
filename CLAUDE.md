# Working on pless

## Language

**Everything is in English** — code, comments, docstrings, user-facing output, tests,
documentation and commit messages. This is an open-source project intended for people other
than its author.

Write neutrally. No "my Pi", no personal names, no references to one particular person's
setup. Examples use placeholders (`<hostname>`, `deploy`, `arkiv-01`) rather than anyone's
real values.

## Documentation must not drift

Documentation lives in `docs/` and is published with Zensical. Treat it as part of the
change, not as follow-up work:

- Adding or renaming a **command** means updating `docs/reference/commands.md`.
- Adding or changing a **config key** means updating `docs/reference/configuration.md`
  and `pless.toml`.
- Changing **behaviour a user relies on** means finding and updating the pages that describe
  it — the cookbook and installation pages quote real console output.

`tests/test_docs_consistency.py` enforces the mechanical half of this and runs in CI. It
fails when a command is undocumented, when the docs describe something that no longer
exists, when a config section is missing from the reference, when an internal link breaks,
or when a page is missing from the navigation.

It cannot check whether the prose is still *true*. When you change what a command prints or
does, reread the pages that quote it.

```bash
uv run pytest tests/test_docs_consistency.py   # the drift guard
zensical serve                                 # preview at http://localhost:8000
zensical build                                 # what CI does
```

## Verify against reality

Unit tests are necessary and not sufficient. Several real bugs in this project were invisible
to tests and obvious the moment the code ran on a machine: package names that differ between
distributions, an apt lock held by cloud-init, a service listening on an interface nobody had
considered.

Before claiming something works, run it:

```bash
pless vm create        # a disposable VM, lima (Debian) or multipass (Ubuntu)
pless bootstrap
pless storage init --confirm
pless deploy paperless
pless audit
pless vm destroy --confirm
```

## Conventions

- **Secrets go on stdin, never in argv.** Anything in `argv` is readable by any local user
  through `ps`.
- **Core modules stay free of `typer` and `rich`.** `cli.py` is the only presentation layer,
  which is what allows a web interface to drive the same code.
- **Destructive commands require `--confirm`,** and the most destructive also require typing
  the name of what will be destroyed.
- **Pin versions deliberately.** Container image tags are exact, so an unattended pull cannot
  carry a database across a migration nobody chose.
- **Be honest in the docs.** Features that do not exist are listed as not existing. A status
  table beats a promise.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Where things are recorded

- **Why the code looks like this** — [`adr/`](adr/README.md). Read the register before
  changing an architectural choice; if you disagree with one, add a superseding record rather
  than silently reversing it. Write a new ADR when a choice would surprise a reader, closes off
  an obvious alternative, or took an argument to reach.
- **What is being worked on** — [GitHub issues](https://github.com/kschulst/pless/issues).
  Not the documentation, and not a file in the repo. The docs say what works today; the
  tracker says what is being done about the rest.
- **How a non-trivial change was designed** — [`spdd/`](spdd/README.md). Analysis and a
  REASONS Canvas, committed before the code. Used for cross-module work and anything touching
  the security model; skipped for bug fixes and small refactors.
- **How to work on it** — [CONTRIBUTING.md](CONTRIBUTING.md).
