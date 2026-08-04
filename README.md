# pless

**A searchable archive of your documents, on hardware you own.**

[![CI](https://github.com/kschulst/pless/actions/workflows/ci.yml/badge.svg)](https://github.com/kschulst/pless/actions/workflows/ci.yml)
[![Docs](https://github.com/kschulst/pless/actions/workflows/docs.yml/badge.svg)](https://kschulst.github.io/pless/)

`pless` sets up and operates a self-hosted [Paperless-ngx](https://docs.paperless-ngx.com/)
installation — on a Raspberry Pi, a cloud server, or a local VM — with two properties most
self-hosting guides skip:

- **Encrypted at rest.** Documents live on a LUKS2 volume whose key is never stored on the
  machine. A stolen box is a brick.
- **Invisible on your LAN.** After hardening, nothing listens on your local network. Paperless
  is bound to localhost and SSH answers only over Tailscale.

> [!WARNING]
> **Alpha.** `pless` cannot yet import documents or take backups — those are the next two
> milestones. Don't put your only copy of anything on it.

## 📖 [Documentation →](https://kschulst.github.io/pless/)

Getting started, installation, maintenance and a cookbook live on the docs site.

## Quick look

```bash
git clone https://github.com/kschulst/pless.git
cd pless
uv tool install --editable .

pless init --secrets
pless doctor
```

Then follow [Installation](https://kschulst.github.io/pless/installation/).

## Status

| Capability | Status |
|---|---|
| Provisioning, encrypted storage, deployment | ✅ Working |
| Tailscale access, LAN hardening, exposure audit | ✅ Working |
| Raspberry Pi target | 📋 Documented, awaiting hardware validation |
| Hetzner Cloud target | 📋 Code and unit tests, not validated live |
| Importing documents | ❌ Not built |
| Backup, restore, restore drills | ❌ Not built |
| Web setup wizard | ❌ Planned |

Verified end to end on Debian 13 and Ubuntu 24.04.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
zensical serve      # docs at http://localhost:8000
```

Design decisions and their reasoning are recorded in [DECISIONS.md](DECISIONS.md) — written
in Norwegian, as the project's working log.

## Licence

MIT. See [LICENSE](LICENSE).
