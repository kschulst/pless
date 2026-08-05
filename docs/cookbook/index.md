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

-   :material-swap-horizontal: **[Switch target](switch-target.md)**

    Rehearsing on a VM before touching real hardware, and moving between Pi, VM and cloud.

</div>

## Recipes that do not exist yet

Two obvious ones are missing because the features are missing. Being straight about that is
more useful than writing instructions that do not work:

- **[Importing your documents](https://github.com/kschulst/pless/issues/1)** — not built.
  `pless docs scan` and `pless docs estimate` already work, so you can survey and size your
  collection today.
- **[Backup and restore](https://github.com/kschulst/pless/issues/2)** — not built, along with
  [restore drills](https://github.com/kschulst/pless/issues/3).

Until those land, keep your originals where they already are. Treat this archive as a
searchable index of documents you still hold elsewhere — and note that `pless preflight` will
tell you the same thing, in as many words.
