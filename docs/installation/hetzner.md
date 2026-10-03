# Hetzner Cloud

Someone else's hardware, always on, no boxes on your shelf. Roughly €4–6 per month for a
CX23, which is ample for this workload.

!!! warning "Not yet validated live"

    The Hetzner target has code and unit tests, but has not been exercised against the real
    API end to end. The provisioning path it uses — cloud-init — *is* validated, via the
    Multipass backend. Expect rough edges, and please report them.

## Trade-offs worth naming

The encryption still works exactly as it does on a Pi: the LUKS key never touches the server.
But be honest with yourself about what that buys you here. Physical theft is no longer your
threat model — Hetzner's data centre is better guarded than your flat. What encryption
protects against is a disk being decommissioned carelessly, a snapshot leaking, or a support
process going wrong.

What you gain is uptime and no hardware to maintain. What you give up is that the machine is
someone else's, subject to their terms and their account suspension policies. That is also
precisely why the off-site backup targets Backblaze B2 rather than Hetzner's own object
storage — a backup that shares a blast radius with the thing it protects is not much of a
backup. `pless b2 provision` sets that bucket up, and the key it mints cannot destroy
history even if this machine is taken.

## Set up

Create a **Read & Write** API token in the Hetzner Cloud Console under
**Security → API tokens**, and put it in `.env`:

```bash
HCLOUD_TOKEN=your-token-here
```

Save it in your password manager too.

```toml
[hetzner]
location = "hel1"        # Helsinki
server_type = "cx23"     # 2 vCPU, 4 GB RAM, 40 GB NVMe
image = "debian-13"
server_name = "paperless-01"
```

Verify the token before doing anything that costs money:

```bash
pless doctor
pless hetzner check-token
```

That performs harmless read-only calls and lists the servers and locations your token can
see.

## Size and cost

`cx23` gives 2 vCPU, 4 GB RAM and 40 GB of NVMe, which fits an archive of a few gigabytes
comfortably — remember that Paperless keeps both the original and an OCR'd archive copy, so
plan for roughly double your source size, plus room for exports.

Verify the current price and specification in the Hetzner console before creating anything.
Both change, and a stale number in documentation is worse than none.

## Then what

Once the server exists and is reachable over SSH, the flow is identical to every other
target:

```bash
pless bootstrap
pless storage init --confirm
pless deploy paperless
pless tailscale up
pless harden --confirm
pless audit
```

The firewall rules matter more here than at home: a cloud server is exposed to the entire
internet rather than just your living room, so `pless harden` is not optional. After it, SSH
answers only over your tailnet, and the server has no public attack surface at all.

## Storage

The default `data_mode = "file"` puts the LUKS volume in a sparse file on the root disk,
which works on any server without partition surgery. If you later attach a Hetzner Volume,
switch to a partition:

```toml
[storage]
data_mode = "partition"
data_device = "/dev/disk/by-id/scsi-0HC_Volume_12345678"
```

Always use a stable `/dev/disk/by-id/...` path. Device names like `/dev/sdb` are assigned in
boot order and will eventually point somewhere you did not intend.
