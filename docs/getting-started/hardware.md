# Hardware

What you actually need, what is merely recommended, and what to avoid. If you already own a
Raspberry Pi 4 or 5, you can probably skip the shopping list entirely.

## Requirements

Only two things are non-negotiable:

**A 64-bit ARM or x86-64 machine.** Paperless-ngx 2.x is not built for 32-bit ARM at all, so
a Pi 1, 2, or Zero is a dead end regardless of what else you do. On a Pi 3 or newer, make
sure you flash a 64-bit image — `pless bootstrap` checks the architecture and refuses to
continue on 32-bit, but it is a frustrating way to discover the problem.

**At least 4 GB of RAM.** The stack runs five containers, and the OCR worker is the hungry
one. 2 GB technically boots, but you will have to drop Tika and Gotenberg, which means Office
documents stop being importable. 4 GB is comfortable; 8 GB is future-proofing.

Everything else — NVMe, a particular case, an SD card — is a preference, not a requirement.
`pless` stores its data in an encrypted *file* on the root filesystem, so the type of storage
medium is invisible to the tool.

## Recommended build

This is what the documentation was written against:

| Part | Choice | Why |
|---|---|---|
| Board | Raspberry Pi 5, 4 GB | Has ARM crypto extensions, so LUKS runs full-speed AES. 2–3× faster OCR than a Pi 4. |
| Storage | NVMe SSD via HAT or case | PostgreSQL writes constantly; SD cards die of it. |
| Power | Official 27 W USB-C | Pi 5 is fussy about power. Underpowered supplies cause crashes that look like software bugs. |
| Cooling | Active cooler | Sustained OCR throttles a passively cooled Pi. |
| Boot media | None needed with NVMe | Pi 5 boots from NVMe directly. |

Expect somewhere around €120–160 for the lot, once. Compared to a year of cloud hosting,
it pays for itself quickly — and nobody else's terms of service apply to it.

## How much space do you need?

Less than you might think. Paperless keeps the original file *and* an OCR'd archive copy, so
budget roughly **2.2×** your collection; local exports add about another **1.2×**. A 5 GB
collection of scans and PDFs therefore lands near 17 GB fully built out.

That means a 256 GB NVMe is not "enough" — it is enormous. The number that matters is
`data_size_gb`, which caps how large the encrypted volume may grow, and it should be set
well below the disk size so a full archive can never starve the operating system. See
[Sizing `data_size_gb`](../reference/configuration.md#sizing) for the arithmetic and a table.

## Choosing storage

The tool works with all of these. The differences are lifespan and speed, not compatibility.

=== "NVMe SSD"

    The right answer if you are buying. Fast, and it survives the constant small writes that
    a database generates. On a Pi 5 it also boots directly, so you need no SD card at all.

    Requires an M.2 **M-key** drive (M.2 SATA will not work), size 2280 or smaller. Note that
    many HATs and cases cap PCIe power at around 5 W — a power-hungry enterprise SSD can cause
    instability that is maddening to diagnose.

=== "USB SSD"

    Nearly as good, and often cheaper if you already have a spare 2.5" drive and an adapter.
    Slower than NVMe, but far more durable than flash media. A perfectly reasonable choice.

=== "microSD"

    Works, and costs nothing if you have one in a drawer. But PostgreSQL will wear it out,
    and SD cards tend to fail *silently* — you get corruption, not an error message.

    If you go this route, treat off-site backup as mandatory rather than optional, and set
    `data_size_gb` well below the card's capacity (say 16 on a 32 GB card). Use an A2-class
    card from a brand you recognise. Plan for the card dying, because it will.

## A note on Pi 4 versus Pi 5

Both are supported and `pless` adapts automatically. The difference that matters is
cryptographic: the Pi 5's Cortex-A76 has ARM crypto extensions, so LUKS uses AES-XTS at full
hardware speed. The Pi 4's Broadcom chip lacks them, so `pless` selects
[Adiantum](https://github.com/google/adiantum) instead — a cipher Google designed for exactly
this situation, giving sound encryption without hardware acceleration.

You do not configure this. `pless storage init` reads `/proc/cpuinfo` and picks. It is
mentioned here only so the choice is not a surprise when you see it in the output.

## Operating system

Both are equally supported and equally tested:

- **Raspberry Pi OS (Trixie), 64-bit Lite** — recommended *on Pi hardware*, because the
  Raspberry Pi Foundation's kernel receives fixes for Pi-specific hardware first. That has
  mattered in practice: NVMe and PCIe bugs on the Pi 5 were fixed there before they reached
  Ubuntu's kernel.
- **Ubuntu Server 24.04 or 26.04 LTS**, 64-bit — a fine choice, and the natural one if you
  are running on a cloud server rather than a Pi.

Whichever you choose, verify it is **64-bit**.

Next: [Installation](../installation/index.md).
