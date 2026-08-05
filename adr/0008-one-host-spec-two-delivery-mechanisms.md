# 0008 — One host specification, two delivery mechanisms

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

Targets differ in how they can be provisioned. A cloud server accepts cloud-init user-data at
creation. A Raspberry Pi installed with Raspberry Pi Imager has already booted before anyone
touches it, so there is no boot partition left to write to.

Writing provisioning twice guarantees the two copies diverge, and the divergence will be
discovered on whichever one is used less.

## Decision

There is one specification of what a `pless` machine must look like — packages, SSH
hardening, firewall, automatic updates, LLMNR disabled — and two renderers over it:
cloud-init user-data, and an idempotent shell script delivered over SSH.

Both are generated from the same constants in `hostspec.py`. Package lists differ per
distribution, and `packages_for()` is the single place that fork exists.

## Consequences

Adding a target requires answering only "how do I get an SSH-reachable machine", not
"how do I provision one".

Both mechanisms are exercised in development: the Lima VM backend runs Debian provisioned
over SSH (mirroring a Pi), and the Multipass backend runs Ubuntu provisioned by cloud-init
(mirroring a cloud server). Neither path can rot unnoticed.

The SSH renderer must be idempotent, since it may run on a machine cloud-init already
provisioned. It must also wait for the apt lock, because a freshly booted machine is often
still running cloud-init or unattended-upgrades.

The shell renderer deliberately never touches `authorized_keys`: we arrived over SSH, so the
key already works, and the script must not be able to lock us out.

## Alternatives considered

- **Cloud-init everywhere:** impossible on an already-booted Pi without reflashing.
- **SSH bootstrap everywhere:** discards a mechanism cloud targets provide for free, and
  makes first boot slower.
- **Ansible or similar:** a heavy dependency for a spec this small, and another tool for
  users to install before they can begin.
