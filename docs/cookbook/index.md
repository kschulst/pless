# Cookbook

Recipes for situations you will actually run into, in rough order of how often you will meet
them.

<div class="grid cards" markdown>

-   :material-power-plug-off: **[After a power loss](power-loss.md)**

    The archive is locked and Paperless is unreachable. This is normal. Here is what to do
    and how to find out sooner next time.

-   :material-shield-check: **[Verify your box is sealed](verify-security.md)**

    Reading `pless audit` finding by finding, including the Docker firewall trap that
    silently exposes ports.

-   :material-harddisk: **[Grow or move storage](storage.md)**

    Running out of room, or moving from an SD card to an SSD, without losing the archive.

-   :material-file-import: **[Importing a collection](importing.md)**

    Getting your documents into the archive: what to expect of throughput, why the first run
    does not finish, and what a failed document means.

-   :material-swap-horizontal: **[Switch target](switch-target.md)**

    Rehearsing on a VM before touching real hardware, and moving between Pi, VM and cloud.

</div>

## What is still missing

Being straight about this is more useful than writing instructions that do not work. Everything
in the grid above is built and drilled. What is not:

- **Updating Paperless in place** — [not built](https://github.com/kschulst/pless/issues/6).
  Image tags are pinned deliberately, so an upgrade is a decision you make by editing
  `pless.toml` and redeploying, not something that happens overnight.

Import, backup, restore and the rehearsal **are** built. `pless docs upload` brings a collection
in; `pless backup init` puts snapshots off-site on a timer; `pless backup verify` proves the
documents come back by restoring them, and `--level full` rehearses the whole procedure on a
machine built from nothing. `pless backup extract` pulls documents out of a snapshot in the clear,
`pless backup forget` thins history, and `pless backup mirror` copies the repository to a second
repository so one provider is not a single point of failure.

**Keep your originals until a backup has included them.** Import reads your files and never moves
or deletes them, so this costs you nothing but patience: `pless backup status` tells you when the
archive has an off-site copy of what you have just added. `pless preflight` says the same thing, in
as many words, before you start.
