# 0016 — There is only a host; provisioning is a separate, optional step

- **Status:** Accepted
- **Date:** 2026-08-05
- **Supersedes:** the `target.type` model

## Context

Configuration used to begin with `target.type = "vm" | "pi" | "hetzner"`, and modules branched
on it. That field conflated two unrelated questions:

1. **How do I create a machine?** — relevant to exactly two commands.
2. **How do I reach it?** — relevant to `bootstrap`, `storage`, `deploy`, `audit`, `preflight`,
   `unlock` and everything else.

The names also lied. The `pi` target was never Pi-specific: it took an address and a user, so
it already worked for a NUC, a VM on a hypervisor, an old laptop, or a droplet at any
provider. Someone with one of those would read the list of targets and conclude the project
was not for them.

The one genuine complication was that a local VM's SSH port changes on every restart, which is
why `vm` appeared to need special handling. But Lima writes an `ssh.config` and rewrites it
when the port changes — so the moving part is already solved, by ssh, in a standard format.

## Decision

There is one kind of target: a machine reachable over SSH, described by `[host]`. Either
connection details directly, or a reference to an SSH config entry:

```toml
[host]
address = "archive.local"
user = "admin"
```
```toml
[host]
ssh_config = "~/.lima/pless-dev/ssh.config"
ssh_alias = "lima-pless-dev"
```

Creating a machine becomes separate and optional. `pless vm create` builds a local VM and
writes `[host]` to point at it; a future `pless hetzner create` will do the same. Multipass
gets an SSH config generated for it, so both backends look identical to the rest of the code.

`storage` branches on `[storage] data_mode`, which is what it was really about.

## Consequences

Every cloud provider is supported without any provider-specific code. Create a machine at AWS,
GCP, Azure, DigitalOcean, Vultr or anywhere else, point `[host]` at it, and everything works.
A provider adapter now buys only the convenience of not clicking, which reframes what looked
like a large roadmap item as optional polish.

The `ssh_config` form gives users their whole existing SSH setup for free — `ProxyJump`
through a bastion, non-standard ports, agent forwarding, multiplexing. If `ssh archive` works
in a terminal, `pless` works against it, and we implement none of it.

Fewer branches: `targets` shrank to resolving one section, and `doctor` and `server status`
stopped switching on machine kind.

The config format changed incompatibly. Done during pre-release with one known user, which is
the cheapest it will ever be.

`pless vm create` now writes to `pless.toml`. Editing a user's configuration is intrusive, so
it refuses to repoint a `[host]` that names a different machine without `--force` — the failure
it prevents is abandoning a machine that holds documents.

`pless vm destroy` gained a matching guard: it refuses when the encrypted volume is unlocked
and mounted, because a local VM can be someone's real archive, and `vm destroy` is a command
muscle memory types quickly.

## Alternatives considered

- **Keep `type` for provisioning only:** a field most users would never set, that still looks
  like it means more than it does.
- **Rename `pi` to `host` and keep the enum:** fixes the lie about Pis, keeps the conflation.
- **Track the VM's port ourselves:** reimplements what Lima already publishes, and goes stale
  the moment a VM restarts outside `pless`.
