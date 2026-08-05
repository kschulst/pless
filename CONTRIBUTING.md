# Contributing

How to work on `pless` itself. If you only want to *use* it, start at the
[documentation site](https://kschulst.github.io/pless/) instead.

Design decisions and their reasoning live in [`adr/`](adr/README.md). Read the register before
changing an architectural choice; if you disagree with one, add a superseding record rather
than silently reversing it.

## Setup

```bash
git clone https://github.com/kschulst/pless.git
cd pless
uv sync
uv run pless --help
```

To use it as a command while developing, so edits take effect immediately:

```bash
uv tool install --editable .
```

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

CI runs all three on every push and pull request.

## Verify against a real machine

Unit tests are necessary and not sufficient. Several real bugs in this project were invisible
to tests and obvious the moment the code ran on a machine: package names that differ between
Debian and Ubuntu, an apt lock held by cloud-init on first boot, a service listening on an
interface nobody had thought about.

Before claiming a change works, run it:

```bash
pless vm create                 # disposable VM
pless bootstrap
pless storage init --confirm    # throwaway passphrase
pless deploy paperless
pless audit
pless vm destroy --confirm
```

Choose the backend that matches what you changed. `lima` gives Debian 13 provisioned over
SSH, mirroring a Raspberry Pi; `multipass` gives Ubuntu provisioned by cloud-init, mirroring
a cloud server. Both distributions are supported equally, so changes to provisioning should
be tried on both.

## Documentation

Documentation lives in `docs/` and is built with [Zensical](https://zensical.org/).

```bash
zensical serve      # http://localhost:8000, rebuilds as you edit
zensical build      # what CI does
```

It deploys to GitHub Pages automatically when `docs/` or `zensical.toml` changes on `main`.

### Drift is caught mechanically

`tests/test_docs_consistency.py` fails when documentation and code disagree — a command that
exists but is undocumented, a documented command that was removed, a config section missing
from the reference, a broken internal link, a page missing from the navigation.

It cannot tell whether the prose is still *true*. When you change what a command prints, find
the pages that quote it and reread them. The cookbook and installation pages contain real
console output.

## Architecture

The core modules import neither `typer` nor `rich`, and return dataclasses:

| Module | Responsibility |
|---|---|
| `config` | Load `pless.toml` and secrets |
| `targets` | Resolve a target to a host reachable over SSH |
| `sshexec` | Run commands over SSH; secrets on stdin |
| `hostspec` | The host specification, as cloud-init or as a shell script |
| `bootstrap` | Apply the host spec over SSH |
| `storage` | The LUKS volume: init, unlock, lock, status |
| `composegen` | Generate the compose file and systemd unit |
| `deploy` | Install and operate the stack |
| `tailscale` | Join the tailnet; close the LAN |
| `audit` | Exposure checks |
| `preflight` | Readiness gate |
| `docscan`, `diskcheck` | Classify and size local documents |
| `cli` | The only presentation layer |

That separation is deliberate: it is what will let a web interface drive the same code rather
than reimplementing it.

## Conventions

- **English everywhere** — code, comments, output, tests, docs, commit messages. Write
  neutrally, with placeholders rather than anyone's real hostnames.
- **Secrets go on stdin, never in argv.** Anything in `argv` is readable by any local user
  through `ps`.
- **Destructive commands require `--confirm`,** and the most destructive also require typing
  the name of what will be destroyed.
- **Pin versions deliberately.** Container tags are exact, so an unattended pull cannot carry
  a database across an unplanned migration.
- **Be honest about what does not exist.** A status table beats a promise.

## Issues

Work to be done lives in [GitHub issues](https://github.com/kschulst/pless/issues) — not in
the documentation, and not in a file in the repository. The documentation says what works
today; the issue tracker says what is being done about the rest.

Labels worth knowing: `area:*` for which part of the system, `type:*` for what kind of work,
and `good first issue` for places to start.

## Releasing

Versions follow [PEP 440](https://peps.python.org/pep-0440/), and **the git tag is the
version** — `hatch-vcs` derives it, so no file holds a version number and nothing can drift
out of sync with what was tagged.

While the project is alpha, releases are pre-releases (`0.1.0a1`, `0.1.0a2`), which means
`pip install pless` will **not** pick them up unless someone asks:

```bash
pip install --pre pless
```

That is deliberate for now: the name is reserved and the pipeline is proven, without anyone
installing an archive tool that cannot yet back itself up.

### Cutting one

Entirely from the GitHub UI:

1. **Releases → Draft a new release**
2. **Choose a tag → create a new tag**, e.g. `v0.1.0a2`
3. **Generate release notes** — GitHub assembles them from the commits since the last release
4. **Publish release**

Publishing creates the tag, which triggers the release workflow. It lints, tests, builds,
installs the wheel into a clean virtualenv and runs `pless init` in an empty directory —
because a package that cannot bootstrap itself from a fresh install is broken in the way that
matters most — then confirms the built version matches the tag before publishing.

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) over
OIDC, so no API token exists anywhere to leak.

Trusted Publishing is already configured on PyPI for this repository. If it ever needs
redoing — a rename, a new repository — the publisher is registered against project `pless`,
owner `kschulst`, repository `pless`, workflow `release.yml`, environment `pypi`. Without a
matching entry, the publish step fails with a permissions error, which is PyPI correctly
refusing an unknown publisher.
