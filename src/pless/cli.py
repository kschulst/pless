"""pless — set up and operate a self-hosted Paperless-ngx installation."""

from __future__ import annotations

import secrets as pysecrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pless import (
    __version__,
    audit,
    bootstrap,
    composegen,
    config,
    deploy,
    diskcheck,
    docscan,
    hetzner,
    hostspec,
    preflight,
    scaffold,
    sshexec,
    storage,
    tailscale,
    targets,
    vm,
)

app = typer.Typer(
    name="pless",
    help="Set up and operate a self-hosted Paperless-ngx installation.",
    no_args_is_help=True,
)
hetzner_app = typer.Typer(help="Hetzner Cloud setup and checks.", no_args_is_help=True)
docs_app = typer.Typer(help="Scan and size local documents.", no_args_is_help=True)
server_app = typer.Typer(help="Target status and operations.", no_args_is_help=True)
vm_app = typer.Typer(help="Local development VM.", no_args_is_help=True)
storage_app = typer.Typer(help="Encrypted data volume on the target.", no_args_is_help=True)
deploy_app = typer.Typer(help="Deploy and operate the Paperless stack.", no_args_is_help=True)
paperless_app = typer.Typer(help="Paperless application operations.", no_args_is_help=True)
tailscale_app = typer.Typer(help="Tailscale access to the target.", no_args_is_help=True)
app.add_typer(hetzner_app, name="hetzner")
app.add_typer(docs_app, name="docs")
app.add_typer(server_app, name="server")
app.add_typer(vm_app, name="vm")
app.add_typer(storage_app, name="storage")
app.add_typer(deploy_app, name="deploy")
app.add_typer(paperless_app, name="paperless")
app.add_typer(tailscale_app, name="tailscale")

console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> None:
    err_console.print(f"[red]✗[/red] {message}")
    raise typer.Exit(code=1)


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TiB"


@app.command()
def version() -> None:
    """Show the pless version."""
    console.print(f"pless {__version__}")


@app.command()
def init(
    with_secrets: bool = typer.Option(
        False, "--secrets", help="Generate strong secrets and write them to .env."
    ),
) -> None:
    """Set up a working directory: pless.toml, .env.example and .env."""
    # Templates ship inside the package, so this works the same whether pless
    # was installed from PyPI or is being run from a clone.
    result = scaffold.scaffold(Path.cwd())
    for name in result.created:
        console.print(f"[green]✓[/green] Created {name}.")
    for name in result.kept:
        console.print(f"[yellow]•[/yellow] {name} already exists — leaving it alone.")

    env_path = Path(".env")
    if env_path.exists():
        console.print("[yellow]•[/yellow] .env already exists — leaving it alone.")
    else:
        shutil.copy(Path(".env.example"), env_path)
        console.print("[green]✓[/green] Created .env from .env.example.")

    if with_secrets:
        content = env_path.read_text()
        generated: list[str] = []
        for key in ("PAPERLESS_ADMIN_PASSWORD", "PAPERLESS_SECRET_KEY", "POSTGRES_PASSWORD"):
            if f"{key}=\n" in content or content.rstrip().endswith(f"{key}="):
                content = content.replace(f"{key}=", f"{key}={pysecrets.token_urlsafe(32)}", 1)
                generated.append(key)
        env_path.write_text(content)
        if generated:
            console.print(f"[green]✓[/green] Generated secrets: {', '.join(generated)}")
            console.print(
                "[bold yellow]! Save these in your password manager NOW — "
                ".env is only a local cache.[/bold yellow]"
            )
        else:
            console.print("[yellow]•[/yellow] All secrets were already set — generated nothing.")

    cfg = config.load_config()
    if cfg.target.type == "hetzner":
        console.print("\nNext: put HCLOUD_TOKEN in .env, then run [bold]pless doctor[/bold].")
    else:
        console.print("\nNext: run [bold]pless doctor[/bold].")


@app.command()
def doctor() -> None:
    """Check the local environment: tools, config, keys and tokens."""
    cfg = config.load_config()
    sec = config.load_secrets()
    problems = 0

    def check(ok: bool, label: str, hint: str = "") -> None:
        nonlocal problems
        if ok:
            console.print(f"[green]✓[/green] {label}")
        else:
            problems += 1
            suffix = f" — {hint}" if hint else ""
            console.print(f"[red]✗[/red] {label}{suffix}")

    def warn(ok: bool, label: str, hint: str = "") -> None:
        if ok:
            console.print(f"[green]✓[/green] {label}")
        else:
            suffix = f" — {hint}" if hint else ""
            console.print(f"[yellow]•[/yellow] {label}{suffix}")

    check(sys.version_info >= (3, 12), f"Python {sys.version.split()[0]} (requires 3.12+)")
    check(shutil.which("ssh") is not None, "ssh found in PATH")
    check(shutil.which("uv") is not None, "uv found in PATH", "https://docs.astral.sh/uv/")
    check(config.find_config_file() is not None, "pless.toml found")
    check(Path(".env").exists(), ".env exists", "run `pless init`")
    check(
        cfg.ssh.key.exists(),
        f"SSH key found ({cfg.ssh.key})",
        "generate one with ssh-keygen -t ed25519",
    )

    console.print(f"\nActive target: [bold]{cfg.target.type}[/bold]")
    if cfg.target.type == "vm":
        tool = "limactl" if cfg.vm.backend == "lima" else "multipass"
        hint = (
            "run `brew install lima`"
            if cfg.vm.backend == "lima"
            else "run `brew install --cask multipass`"
        )
        check(shutil.which(tool) is not None, f"{tool} is installed ({cfg.vm.backend})", hint)
    elif cfg.target.type == "pi":
        check(bool(cfg.pi.host), "[pi] host is set in pless.toml")
        if cfg.pi.data_mode == "partition":
            check(
                bool(cfg.pi.data_device),
                "[pi] data_device is set (required by data_mode=partition)",
            )
        else:
            console.print(
                f"[green]✓[/green] [pi] data_mode=file ({cfg.pi.data_size_gb} GB LUKS file)"
            )
    elif cfg.target.type == "hetzner":
        check(
            bool(sec.hcloud_token),
            "HCLOUD_TOKEN is set",
            "put it in .env; source of truth is your password manager",
        )

    if cfg.access.mode == "tailscale" and cfg.target.type != "vm":
        warn(
            shutil.which("tailscale") is not None,
            "tailscale CLI found locally",
            "needed at deploy time — https://tailscale.com/download",
        )
    warn(
        bool(sec.paperless_admin_password),
        "Paperless secrets generated",
        "run `pless init --secrets`",
    )

    if problems:
        _fail(f"{problems} problem(s) need fixing.")
    next_step = "pless vm create" if cfg.target.type == "vm" else "pless server status"
    console.print(f"\n[green bold]All clear.[/green bold] Next: [bold]{next_step}[/bold]")


@hetzner_app.command("check-token")
def hetzner_check_token() -> None:
    """Verify HCLOUD_TOKEN against the Hetzner API with read-only calls."""
    sec = config.load_secrets()
    cfg = config.load_config()
    try:
        client = hetzner.make_client(sec.hcloud_token)
        info = hetzner.check_token(client)
    except Exception as exc:  # hcloud raises provider-specific exceptions
        _fail(f"Token check failed: {exc}")
        return

    console.print("[green]✓[/green] Token is valid.")
    console.print(f"  Servers in the project: {info.server_count} {info.server_names or ''}")
    console.print(f"  Locations available: {', '.join(info.locations)}")
    if cfg.hetzner.location not in info.locations:
        console.print(
            f"[yellow]•[/yellow] Configured location {cfg.hetzner.location} is not in that list."
        )


@docs_app.command("scan")
def docs_scan(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Directory to scan."),
    hashes: bool = typer.Option(False, "--hashes", help="Compute sha256 and report duplicates."),
) -> None:
    """Scan a local directory: classify files and find anything needing conversion."""
    result = docscan.scan(path, with_hashes=hashes)

    table = Table(title=f"Scan of {path}")
    table.add_column("Category")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    table.add_row(
        "Ready for Paperless", str(len(result.supported)), _human_size(result.supported_bytes)
    )
    table.add_row(
        "Needs conversion (.enex/.html)",
        str(len(result.needs_conversion)),
        _human_size(result.needs_conversion_bytes),
    )
    table.add_row("Unsupported/ignored", str(len(result.unsupported)), "—")
    console.print(table)

    top = result.by_extension.most_common(10)
    console.print("Most common file types: " + ", ".join(f"{ext} ({n})" for ext, n in top))

    if result.needs_conversion:
        console.print(
            "\n[yellow]•[/yellow] Files needing conversion were found. Import support for "
            "them is not built yet."
        )
    if hashes:
        dupes = result.duplicate_groups()
        if dupes:
            dup_files = sum(len(v) - 1 for v in dupes.values())
            console.print(
                f"[yellow]•[/yellow] {dup_files} duplicate file(s) across {len(dupes)} groups "
                "with identical content. Paperless rejects duplicates itself; this is the count."
            )
        else:
            console.print("[green]✓[/green] No duplicate content.")


@docs_app.command("estimate")
def docs_estimate(
    path: Path = typer.Argument(..., exists=True, file_okay=False),
    remote: bool = typer.Option(
        True,
        "--remote/--local-only",
        help="Check actual disk usage on the target over SSH.",
    ),
) -> None:
    """Estimate disk needs after import and compare with the target's disk."""
    cfg = config.load_config()
    result = docscan.scan(path)
    upload = result.supported_bytes + result.needs_conversion_bytes

    console.print(f"Upload volume, including unconverted files: {_human_size(upload)}")
    console.print(
        f"Estimated growth on the target (x{diskcheck.INGEST_GROWTH_FACTOR} ingest "
        f"+ x{diskcheck.EXPORT_COPY_FACTOR} export copy): "
        f"{_human_size(upload * (diskcheck.INGEST_GROWTH_FACTOR + diskcheck.EXPORT_COPY_FACTOR))}"
    )

    if not remote:
        return

    snapshot = _remote_df(cfg)
    projection = diskcheck.project(
        snapshot,
        upload,
        min_free_gb=cfg.storage.min_free_gb_after_upload,
        max_used_percent=cfg.storage.max_disk_usage_percent_after_upload,
    )
    console.print(
        f"Target disk: {_human_size(snapshot.total_bytes)} total, "
        f"{snapshot.used_percent:.0f}% used now, "
        f"{projection.projected_used_percent:.0f}% after import, "
        f"{projection.projected_free_gb:.1f} GB free."
    )
    match projection.recommendation:
        case diskcheck.Recommendation.PROCEED:
            console.print("[green bold]✓ Safe to proceed.[/green bold]")
        case diskcheck.Recommendation.REDUCE_BATCH:
            console.print(
                "[yellow bold]! Over the threshold — import in smaller batches and clean up "
                "export copies as you go, or add storage.[/yellow bold]"
            )
        case diskcheck.Recommendation.ADD_STORAGE:
            console.print(
                "[red bold]✗ The disk is too small for this volume — add storage or choose a "
                "larger machine.[/red bold]"
            )


def _resolve_target(cfg: config.Config) -> targets.TargetHost:
    try:
        return targets.resolve_target(cfg, config.load_secrets())
    except (targets.TargetError, ValueError) as exc:
        _fail(str(exc))
        raise  # unreachable; helps the type checker


def _remote_df(cfg: config.Config, mount: str = "/") -> diskcheck.DiskSnapshot:
    target = _resolve_target(cfg)
    result = sshexec.run(target.user, target.host, target.key, f"df -Pk {mount}", port=target.port)
    if not result.ok:
        _fail(f"SSH/df feilet mot {target.host}: {result.stderr.strip()}")
    return diskcheck.parse_df_output(result.stdout)


@server_app.command("df")
def server_df() -> None:
    """Show disk usage on the active target."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    snapshot = _remote_df(cfg)
    console.print(
        f"{target.name} ({target.host}): {_human_size(snapshot.total_bytes)} total, "
        f"{_human_size(snapshot.used_bytes)} used ({snapshot.used_percent:.0f}%), "
        f"{_human_size(snapshot.avail_bytes)} free."
    )


@server_app.command("status")
def server_status() -> None:
    """Show the status of the active target."""
    cfg = config.load_config()
    if cfg.target.type == "vm":
        try:
            vm.require_backend(cfg.vm.backend)
            data = vm.info(cfg.vm)
        except vm.VmError as exc:
            _fail(str(exc))
            return
        addresses = ", ".join(data["addresses"] or ["-"])
        console.print(
            f"{cfg.vm.name} ({cfg.vm.backend}): [bold]{data['state']}[/bold], {addresses}"
        )
        return
    if cfg.target.type == "hetzner":
        sec = config.load_secrets()
        client = hetzner.make_client(sec.hcloud_token)
        server = hetzner.get_server(client, cfg.hetzner.server_name)
        if server is None:
            console.print(f"[yellow]•[/yellow] No server named {cfg.hetzner.server_name!r} yet.")
            return
        console.print(
            f"{server.name}: [bold]{server.status}[/bold], "
            f"type {server.server_type.name}, {server.datacenter.name}, "
            f"IPv4 {hetzner.server_ip(server)}"
        )
        return
    target = _resolve_target(cfg)
    result = sshexec.run(target.user, target.host, target.key, "uptime", port=target.port)
    if result.ok:
        console.print(f"{target.name} ({target.host}): up — {result.stdout.strip()}")
    else:
        console.print(f"[red]✗[/red] {target.name} ({target.host}): unreachable over SSH")


@app.command()
def ssh() -> None:
    """Open an interactive SSH session on the active target."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    raise typer.Exit(
        subprocess.call(
            [
                "ssh",
                "-p",
                str(target.port),
                "-i",
                str(target.key),
                f"{target.user}@{target.host}",
            ]
        )
    )


@vm_app.command("create")
def vm_create() -> None:
    """Create the development VM."""
    cfg = config.load_config()
    try:
        vm.require_backend(cfg.vm.backend)
    except vm.VmError as exc:
        _fail(str(exc))
    if vm.exists(cfg.vm):
        console.print(f"[yellow]•[/yellow] VM {cfg.vm.name!r} already exists.")
        return

    user_data_path: Path | None = None
    if cfg.vm.backend == "multipass":
        try:
            pubkey = hostspec.read_pubkey(cfg.ssh.key)
        except FileNotFoundError as exc:
            _fail(str(exc))
            return
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(hostspec.render_user_data(pubkey, cfg.paperless.timezone))
            user_data_path = Path(f.name)

    console.print(
        f"Creating {cfg.vm.name} via {cfg.vm.backend} "
        f"({cfg.vm.cpus} vCPU, {cfg.vm.memory}, {cfg.vm.disk}). "
        "The first run downloads an image, which takes a few minutes…"
    )
    try:
        vm.launch(cfg.vm, user_data_path)
    except vm.VmError as exc:
        _fail(str(exc))
    finally:
        if user_data_path:
            user_data_path.unlink(missing_ok=True)

    data = vm.info(cfg.vm)
    next_step = "pless bootstrap" if cfg.vm.backend == "lima" else "pless storage init --confirm"
    console.print(
        f"[green]✓[/green] {cfg.vm.name} is up: {', '.join(data['addresses'])}. "
        f"Next: [bold]{next_step}[/bold]"
    )


@vm_app.command("destroy")
def vm_destroy(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm deleting the VM."),
) -> None:
    """Delete the development VM and everything on it. Destructive."""
    cfg = config.load_config()
    if not confirm:
        _fail(f"This deletes VM {cfg.vm.name!r} and everything on it. Run again with --confirm.")
    typed = typer.prompt(f"Type the VM name ({cfg.vm.name}) to confirm")
    if typed != cfg.vm.name:
        _fail("The name did not match — aborting.")
    try:
        vm.delete(cfg.vm)
    except vm.VmError as exc:
        _fail(str(exc))
    console.print(f"[green]✓[/green] {cfg.vm.name} has been deleted.")


@app.command("audit")
def audit_cmd(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Audit exposure: what can someone on your network actually reach?"""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    result = sshexec.run(
        target.user,
        target.host,
        target.key,
        "sh -s",
        timeout=120,
        input_text=audit.COLLECT_SCRIPT,
        port=target.port,
    )
    if not result.ok:
        _fail(f"Collection failed: {result.stderr.strip()}")
    report = audit.analyse(result.stdout)

    if json_output:
        console.print_json(
            data={
                "ok": report.ok,
                "findings": [
                    {
                        "check": f.check,
                        "ok": f.ok,
                        "severity": str(f.severity),
                        "detail": f.detail,
                    }
                    for f in report.findings
                ],
            }
        )
    else:
        for finding in report.findings:
            mark = "[green]✓[/green]" if finding.ok else "[red]✗[/red]"
            console.print(f"{mark} {finding.check}: {finding.detail}")

    if not report.ok:
        raise typer.Exit(code=1)


@tailscale_app.command("status")
def tailscale_status() -> None:
    """Show Tailscale status on the target."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    state = tailscale.status(target)
    if not state.installed:
        console.print("[yellow]•[/yellow] Tailscale is not installed — run `pless tailscale up`.")
        return
    mark = "[green]✓[/green]" if state.is_up else "[red]✗[/red]"
    console.print(f"{mark} {state.backend_state} — {state.hostname or '(unnamed)'}")
    console.print(f"  Addresses: {', '.join(state.addresses) or '-'}")


@tailscale_app.command("up")
def tailscale_up() -> None:
    """Install Tailscale and join the tailnet chosen by TS_AUTHKEY."""
    cfg = config.load_config()
    sec = config.load_secrets()
    target = _resolve_target(cfg)

    state = tailscale.status(target)
    if not state.installed:
        console.print("Installing Tailscale…")
        try:
            tailscale.install(target)
        except tailscale.TailscaleError as exc:
            _fail(str(exc))
    try:
        state = tailscale.up(cfg, target, sec.ts_authkey, cfg.tailscale.hostname)
    except tailscale.TailscaleError as exc:
        _fail(str(exc))
        return
    console.print(
        f"[green]✓[/green] Joined the tailnet as [bold]{state.hostname}[/bold] "
        f"({', '.join(state.addresses)})."
    )
    console.print("Next: [bold]pless harden[/bold] to close SSH to the LAN.")


@app.command("harden")
def harden_cmd(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm closing SSH to the LAN."),
) -> None:
    """Close SSH to the LAN — only the tailnet gets in afterwards."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    if not confirm:
        _fail(
            "This closes SSH to everything except Tailscale. If you lose tailnet access, "
            "your only way back in is a monitor and keyboard. Run again with --confirm."
        )
    try:
        tailscale.harden(target)
    except tailscale.TailscaleError as exc:
        _fail(str(exc))
        return
    console.print(
        "[green]✓[/green] SSH now accepts connections only over tailscale0. "
        "Verify with [bold]pless audit[/bold]."
    )


@app.command("preflight")
def preflight_cmd(
    drill: bool = typer.Option(
        False,
        "--drill",
        help="Also lock and unlock the volume to prove the passphrase works.",
    ),
) -> None:
    """Check whether this installation is fit to be trusted with documents."""
    cfg = config.load_config()
    target = _resolve_target(cfg)

    reachable = sshexec.run(target.user, target.host, target.key, "true", port=target.port).ok

    storage_ready = False
    if reachable:
        try:
            state = storage.status(cfg, target)
            storage_ready = state.is_luks and state.is_open and state.is_mounted
        except storage.StorageError:
            storage_ready = False

    healthy = False
    if storage_ready:
        try:
            healthy = deploy.http_status(target) in ("200", "302")
        except deploy.DeployError:
            healthy = False

    audit_clean = False
    if reachable:
        result = sshexec.run(
            target.user,
            target.host,
            target.key,
            "sh -s",
            timeout=120,
            input_text=audit.COLLECT_SCRIPT,
            port=target.port,
        )
        audit_clean = result.ok and audit.analyse(result.stdout).ok

    drill_passed: bool | None = None
    if drill:
        if not storage_ready:
            _fail("Cannot run the drill: the volume is not unlocked and mounted.")
        console.print(
            "[bold]Drill:[/bold] locking the volume, then unlocking it again. "
            "Paperless will be briefly unavailable."
        )
        passphrase = typer.prompt("LUKS passphrase", hide_input=True)
        drill_passed = preflight.run_drill(
            lock=lambda: not storage.lock(cfg, target).is_mounted,
            unlock=lambda: storage.unlock(cfg, target, passphrase).is_mounted,
            health=lambda: deploy.wait_healthy(target, timeout_seconds=180),
        )

    report = preflight.analyse(
        target_reachable=reachable,
        storage_ready=storage_ready,
        paperless_healthy=healthy,
        audit_clean=audit_clean,
        drill_passed=drill_passed,
        backup_configured=bool(cfg.backup.restic_repository),
        backup_verified=False,  # no verified restore exists until backup is built
    )

    for check in report.checks:
        mark = "[green]✓[/green]" if check.passed else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")

    style = "green bold" if report.readiness is preflight.Readiness.READY else "yellow bold"
    console.print(f"\n[{style}]{report.verdict}[/{style}]")

    if report.blockers:
        raise typer.Exit(code=1)


@app.command("bootstrap")
def bootstrap_cmd() -> None:
    """Apply the host spec over SSH to a machine that has already booted."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        facts = bootstrap.gather_facts(target)
    except bootstrap.BootstrapError as exc:
        _fail(str(exc))
        return
    console.print(
        f"Target: [bold]{facts.hostname}[/bold] ({facts.model}) — {facts.os_pretty_name}, "
        f"{facts.architecture}, {facts.memory_gb} GB RAM"
    )
    console.print("Installing Docker, UFW, fail2ban and automatic updates…")
    try:
        bootstrap.apply(cfg, target)
    except bootstrap.BootstrapError as exc:
        _fail(str(exc))
        return
    console.print(
        "[green]✓[/green] Host spec applied. Next: [bold]pless storage init --confirm[/bold]"
    )


def _storage_status_line(state: storage.StorageStatus) -> str:
    def mark(ok: bool) -> str:
        return "[green]✓[/green]" if ok else "[red]✗[/red]"

    return (
        f"{state.device}: LUKS {mark(state.is_luks)}  "
        f"open {mark(state.is_open)}  mounted at {storage.MOUNTPOINT} {mark(state.is_mounted)}"
    )


@storage_app.command("init")
def storage_init(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm formatting the data volume."),
) -> None:
    """Format the data volume as LUKS2 with ext4. Destructive."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    if not confirm:
        _fail("This FORMATS the data volume on the target. Run again with --confirm.")
    passphrase = typer.prompt(
        "Choose a LUKS passphrase (save it in your password manager FIRST)",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        device = storage.init(cfg, target, passphrase)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] {device} is LUKS-formatted, ext4 and mounted.")
    console.print(_storage_status_line(storage.status(cfg, target)))
    console.print(
        "[bold yellow]! The passphrase exists only in your head and your password "
        "manager. Without it the volume is, by design, worthless.[/bold yellow]"
    )


@storage_app.command("status")
def storage_status_cmd() -> None:
    """Show the LUKS status of the data volume on the active target."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(_storage_status_line(storage.status(cfg, target)))
    except storage.StorageError as exc:
        _fail(str(exc))


@app.command()
def unlock() -> None:
    """Unlock and mount the data volume after a reboot, then start the stack."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    passphrase = typer.prompt("LUKS passphrase", hide_input=True)
    try:
        state = storage.unlock(cfg, target, passphrase)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] Unlocked. {_storage_status_line(state)}")


@app.command()
def lock() -> None:
    """Stop the stack, unmount and lock the data volume."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        state = storage.lock(cfg, target)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] Locked. {_storage_status_line(state)}")


@deploy_app.command("paperless")
def deploy_paperless() -> None:
    """Write the compose stack and systemd unit to the target, then start Paperless."""
    cfg = config.load_config()
    sec = config.load_secrets()
    target = _resolve_target(cfg)
    console.print(
        f"Deploying to {target.name} ({target.host}). The first run pulls ~2 GB of images…"
    )
    try:
        deploy.install(cfg, sec, target)
    except (deploy.DeployError, storage.StorageError, ValueError) as exc:
        _fail(str(exc))
        return
    console.print("[green]✓[/green] Stack started. Waiting for the web server to answer…")
    if deploy.wait_healthy(target):
        console.print(
            f"[green bold]✓ Paperless is up.[/green bold] "
            f"Run [bold]pless tunnel[/bold] and open http://localhost:{composegen.WEB_PORT} "
            f"(user: {cfg.paperless.admin_user}, password: PAPERLESS_ADMIN_PASSWORD from .env)."
        )
    else:
        console.print(
            "[yellow]•[/yellow] The web server is not answering yet — the first start "
            "migrates the database and can take several minutes. "
            "Watch it with [bold]pless deploy logs[/bold]."
        )


@deploy_app.command("status")
def deploy_status() -> None:
    """Show container status for the stack."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(deploy.compose(target, "ps --format table"))
    except deploy.DeployError as exc:
        _fail(str(exc))


@deploy_app.command("logs")
def deploy_logs(
    service: str = typer.Argument("", help="Service (webserver, db, broker, gotenberg, tika)."),
    tail: int = typer.Option(50, "--tail", help="Number of lines."),
) -> None:
    """Show logs from the stack."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(deploy.compose(target, f"logs --tail {tail} {service}".strip(), timeout=60))
    except deploy.DeployError as exc:
        _fail(str(exc))


@paperless_app.command("health")
def paperless_health() -> None:
    """Check that Paperless answers on the target's localhost."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        code = deploy.http_status(target)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return
    if code in ("200", "302"):
        console.print(f"[green]✓[/green] Paperless is answering (HTTP {code}).")
    else:
        _fail(f"Paperless is not answering as expected (HTTP {code}). See `pless deploy logs`.")


@app.command()
def tunnel() -> None:
    """Open an SSH tunnel to Paperless at http://localhost:8000. Ctrl-C closes it."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    console.print(
        f"Tunnel open: [bold]http://localhost:{composegen.WEB_PORT}[/bold] "
        f"-> {target.name} ({target.host}). Ctrl-C to close."
    )
    raise typer.Exit(
        subprocess.call(
            [
                "ssh",
                "-p",
                str(target.port),
                "-i",
                str(target.key),
                "-L",
                f"{composegen.WEB_PORT}:127.0.0.1:{composegen.WEB_PORT}",
                "-N",
                f"{target.user}@{target.host}",
            ]
        )
    )


if __name__ == "__main__":
    app()
