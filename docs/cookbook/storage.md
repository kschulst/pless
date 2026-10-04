# Grow or move storage

Your archive outgrew its disk, or the SD card you started with is showing its age and you
want it on an SSD. Both are the same job: move an encrypted volume without losing it.

!!! danger "You need a copy first"

    Every procedure here touches the data volume. Take a backup and confirm it before you start:

    ```console
    $ pless backup run
    $ pless backup verify
    ```

    `verify` restores from the snapshot and checks the documents came back, which is the only
    thing that distinguishes a backup from a belief. If backup is not configured yet, copy
    `/opt/paperless` somewhere else by hand — see [Copy it out by hand](#copy-it-out-by-hand)
    below. Skipping both is how people lose archives.

## How much room do you need?

```bash
pless server df                              # what the target has now
pless docs estimate ~/Documents --local-only # what an import would add
```

Paperless keeps the original **and** an OCR'd archive copy, and exports add another copy. The
estimator uses deliberately pessimistic multipliers and gives you a verdict — proceed, import
in smaller batches, or add storage — rather than a number to interpret.

Its recommendation to *add storage* means the disk is too small no matter how you batch the
import. Batching only helps when the problem is peak usage during import, not the end state.

## Grow the LUKS file

If `data_mode = "file"` — the default — the volume is a sparse file on the root filesystem.
Growing it needs no new hardware, only free space underneath.

Raise the size in `pless.toml`:

```toml
[storage]
data_size_gb = 400   # was 100
```

Then, on the target:

```bash
pless lock                    # stop the stack, unmount, close the volume
pless ssh
```

```bash
sudo truncate -s 400G /var/lib/pless/data.img
sudo losetup -c $(losetup -j /var/lib/pless/data.img | cut -d: -f1)
exit
```

```bash
pless unlock
pless ssh
```

```bash
sudo cryptsetup resize paperless-data
sudo resize2fs /dev/mapper/paperless-data
exit
```

```bash
pless server df   # confirm the new size
```

Growing an ext4 filesystem is safe and can be done while mounted. **Shrinking is not**, and
is not covered here — moving to a smaller volume means creating a fresh one and copying.

## Move to a different disk

The clean approach: create a new volume on the new disk and copy across. It is slower than
cloning, but every step is reversible until the last one.

Attach the new disk, then find its stable identifier:

```bash
pless ssh
ls -l /dev/disk/by-id/
```

Use a `by-id` path. Names like `/dev/sda` are assigned in boot order and will eventually
point at something you did not intend — which, for a command that formats things, is a very
bad day.

Copy the data with both volumes mounted:

```bash
pless unlock                       # old volume mounted at /opt/paperless
pless ssh
```

```bash
# Format the new disk as LUKS, using the same passphrase
sudo cryptsetup luksFormat --type luks2 /dev/disk/by-id/YOUR-DISK-ID
sudo cryptsetup open /dev/disk/by-id/YOUR-DISK-ID paperless-new
sudo mkfs.ext4 -L paperless /dev/mapper/paperless-new
sudo mkdir -p /mnt/new
sudo mount /dev/mapper/paperless-new /mnt/new

# Stop the stack so nothing writes while copying
sudo systemctl stop paperless.service

# Copy, preserving ownership and permissions
sudo rsync -aHAX --info=progress2 /opt/paperless/ /mnt/new/
```

Verify before you commit to it:

```bash
sudo du -sh /opt/paperless /mnt/new     # sizes should match closely
sudo diff -rq /opt/paperless /mnt/new   # slow but thorough
```

Then switch over:

```bash
sudo umount /mnt/new
sudo cryptsetup close paperless-new
exit
```

```toml
[storage]
data_mode = "partition"
data_device = "/dev/disk/by-id/YOUR-DISK-ID"
```

```bash
pless lock
pless unlock
pless storage status
pless deploy status
pless paperless health
```

Leave the old volume untouched until you have confirmed the archive works — documents
present, search returning results. It is your rollback.

## Copy it out by hand

`pless backup` exists and is the better answer — this is for when it is not configured yet, or
when you want a copy that does not depend on restic or a passphrase at all:

```bash
pless unlock
pless ssh
sudo tar czf /tmp/paperless-backup.tar.gz -C /opt/paperless .
exit

# from your laptop
scp <user>@<host>:/tmp/paperless-backup.tar.gz ./
```

This is a plaintext archive of your documents, so treat it accordingly: it is the one copy that is
*not* encrypted ([ADR 0018](https://github.com/kschulst/pless/blob/main/adr/0018-no-plaintext-copy-on-the-operators-machine.md)
is why `pless` does not make one for you). Delete it from `/tmp` on the target when you are done,
and do not leave it on a laptop.

With backup configured, prefer:

```console
$ pless backup run       # a snapshot now
$ pless backup verify    # restore it and check the documents came back
```

`pless backup extract` pulls documents back out in the clear when you actually need the files
rather than the archive.

For a large archive, `rsync` directly to your laptop avoids needing double the space on the
target:

```bash
rsync -aHAX --info=progress2 <user>@<host>:/opt/paperless/ ./paperless-backup/
```

Note what this is and is not: a copy of files, taken while the database may be mid-write.
It is far better than nothing, and it is not a consistent database backup. The real thing —
Paperless's own exporter plus encrypted restic snapshots, with restore drills — is the next
milestone.

## From file to partition

If you started with the default file-based volume and later added a dedicated disk, the move
is exactly the [disk migration above](#move-to-a-different-disk). The two modes differ only
in where the encrypted block device comes from; everything above that layer is identical.
