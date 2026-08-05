# 0002 — Choose the LUKS cipher automatically from the CPU

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

`pless` targets Raspberry Pi 4 and 5 as well as x86-64 servers. The Pi 4's Broadcom SoC lacks
the ARM crypto extensions, so AES runs in software and encryption becomes a bottleneck for a
workload that writes constantly: OCR output, thumbnails, database pages.

The Pi 5 and every x86-64 server have hardware AES.

## Decision

`pless storage init` reads `/proc/cpuinfo` and selects the cipher itself: AES-XTS where AES
instructions are present, [Adiantum](https://github.com/google/adiantum) otherwise — the
cipher Google designed for exactly this class of hardware.

This is not configurable. There is no good reason for an operator to override it.

## Consequences

Encryption performs well on every supported machine without anyone having to read a benchmark
or know what their SoC supports.

A LUKS volume carries its cipher with it, so moving a disk from a Pi 4 to a Pi 5 keeps
Adiantum until the volume is recreated. Acceptable: it still works, it simply does not use
the newer hardware's advantage.

Detection must match whole tokens in the `Features`/`flags` line. An early version matched
substrings and would have treated a CPU advertising `aesthetics` as AES-capable — caught by a
test written specifically for that trap.

## Alternatives considered

- **Always AES-XTS:** simple, and painfully slow on a Pi 4.
- **A configuration option:** pushes a decision onto operators that the machine can answer
  correctly by itself.
