"""pless — set up and operate a self-hosted Paperless-ngx installation."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pless import (
    __version__,
    audit,
    b2,
    backup,
    bootstrap,
    composegen,
    config,
    deploy,
    diskcheck,
    docscan,
    drill,
    hetzner,
    hostfile,
    hostspec,
    preflight,
    scaffold,
    secretgen,
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
b2_app = typer.Typer(
    help="Backblaze B2 setup. Optional, and only a way to obtain a bucket.",
    no_args_is_help=True,
)
docs_app = typer.Typer(help="Scan and size local documents.", no_args_is_help=True)
server_app = typer.Typer(help="Target status and operations.", no_args_is_help=True)
vm_app = typer.Typer(help="Local development VM.", no_args_is_help=True)
storage_app = typer.Typer(help="Encrypted data volume on the target.", no_args_is_help=True)
deploy_app = typer.Typer(help="Deploy and operate the Paperless stack.", no_args_is_help=True)
paperless_app = typer.Typer(help="Paperless application operations.", no_args_is_help=True)
tailscale_app = typer.Typer(help="Tailscale access to the target.", no_args_is_help=True)
secrets_app = typer.Typer(help="Generate secrets in the documented formats.", no_args_is_help=True)
backup_app = typer.Typer(help="Back up the archive, and prove it restores.", no_args_is_help=True)
app.add_typer(hetzner_app, name="hetzner")
app.add_typer(b2_app, name="b2")
app.add_typer(docs_app, name="docs")
app.add_typer(server_app, name="server")
app.add_typer(vm_app, name="vm")
app.add_typer(storage_app, name="storage")
app.add_typer(deploy_app, name="deploy")
app.add_typer(paperless_app, name="paperless")
app.add_typer(tailscale_app, name="tailscale")
app.add_typer(secrets_app, name="secrets")
app.add_typer(backup_app, name="backup")

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
        # Machine secrets: nobody types these, so they are as long as the
        # receiving software tolerates. See `pless secrets generate`.
        for key in ("PAPERLESS_ADMIN_PASSWORD", "PAPERLESS_SECRET_KEY", "POSTGRES_PASSWORD"):
            if f"{key}=\n" in content or content.rstrip().endswith(f"{key}="):
                content = content.replace(f"{key}=", f"{key}={secretgen.machine_token()}", 1)
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

    console.print(
        "\nNext: point [bold]\\[host][/bold] in pless.toml at a machine, or run "
        "[bold]pless vm create[/bold] to have a local one made for you. "
        "Then [bold]pless doctor[/bold]."
    )


class SecretKind(StrEnum):
    # Which format a secret gets follows from who has to type it.
    HUMAN = "human"
    MACHINE = "machine"


@secrets_app.command("generate")
def secrets_generate(
    kind: SecretKind = typer.Option(
        SecretKind.HUMAN,
        "--kind",
        help="human: typed from a vault or paper. machine: never typed by anyone.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the reminder on stderr."),
) -> None:
    """Print one secret in the documented format, and nothing else.

    Use `--kind human` for the LUKS passphrase and RESTIC_PASSWORD: both are
    unrecoverable, so both are the ones you may one day read off paper.
    """
    value = secretgen.human_passphrase() if kind is SecretKind.HUMAN else secretgen.machine_token()
    # Deliberately not console.print: no markup, no wrapping and no colour, so
    # this can be piped straight into a password manager's CLI.
    typer.echo(value)
    if not quiet:
        err_console.print(
            "[yellow]•[/yellow] Save it now. pless keeps no copy, and terminal "
            "scrollback is not a password manager."
        )


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

    console.print("\n[bold]Host[/bold]")
    check(
        cfg.host.is_configured,
        f"\\[host] points at {cfg.host.label}",
        "set `address` and `user`, or run `pless vm create`",
    )
    if cfg.host.uses_ssh_config:
        path = Path(cfg.host.ssh_config).expanduser()
        check(path.is_file(), f"SSH config exists ({path})", "recreate the VM, or edit \\[host]")
    elif cfg.host.address:
        check(
            cfg.host.key.exists(),
            f"SSH key found ({cfg.host.key})",
            "generate one with ssh-keygen -t ed25519",
        )

    if cfg.storage.data_mode == "partition":
        check(
            bool(cfg.storage.data_device),
            "\\[storage] data_device is set (required by data_mode=partition)",
        )
    else:
        console.print(
            f"[green]✓[/green] \\[storage] data_mode=file ({cfg.storage.data_size_gb} GB LUKS file)"
        )

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
    console.print("\n[green bold]All clear.[/green bold] Next: [bold]pless bootstrap[/bold]")


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


def _fail_with_remedy(exc: b2.B2Error) -> None:
    """Report a refusal, and put its remedy somewhere runnable.

    `b2` raises refusals that carry a script rather than printing one. A
    verification run showed why: printing it inline came to 51 lines, of which
    36 were the script, so the three sentences that mattered scrolled away —
    and twice in one session the wrong thing got run because a long block had
    to be pasted by hand.

    The script holds no secret; it prompts for the master key and writes
    nothing. So it is safe on disk, and one line to run beats thirty-six to
    paste.
    """
    if not exc.script:
        _fail(str(exc))
        return

    path = Path(b2.BOOTSTRAP_SCRIPT_PATH)
    try:
        path.write_text(exc.script, encoding="utf-8")
    except OSError as write_error:
        # Falling back to printing it is worse, and still better than nothing.
        console.print(f"[red]✗[/red] {exc}")
        console.print(f"[yellow]•[/yellow] Could not write {path}: {write_error}")
        _print_verbatim(exc.script)
        raise typer.Exit(code=1) from None

    console.print(f"[red]✗[/red] {exc}")
    console.print("\n[bold]Run this to mint a credential that works:[/bold]\n")
    _print_verbatim(f"    python3 {path}")
    console.print(
        f"\n[yellow]•[/yellow] It prompts for your Backblaze master key, uses it once, and "
        f"stores nothing. Delete {path} afterwards."
    )
    raise typer.Exit(code=1)


class _NoCredentialInput(RuntimeError):
    pass


def _read_credential_pair(id_prompt: str, secret_prompt: str) -> tuple[str, str]:
    """Two credential halves, prompted on a terminal and piped otherwise.

    `typer.prompt(hide_input=True)` reaches for `getpass`, which needs a
    controlling terminal to turn echo off. Without one it raises `EOFError`,
    which is how a script, CI, or a future web wizard would see this command
    fail — a traceback instead of a message, which is the shape of bug #13 and
    #19 were about.

    So a pipe is a supported way in: two lines, id then secret. Neither ever
    reaches argv, which is the property that matters (ADR 0014).
    """
    if sys.stdin is not None and sys.stdin.isatty():
        return typer.prompt(id_prompt), typer.prompt(secret_prompt, hide_input=True)

    lines = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if len(lines) < 2:
        raise _NoCredentialInput(
            "No terminal to prompt on, and stdin did not carry a credential. Pipe two "
            "lines — the keyID, then the applicationKey — or run this from a terminal. "
            "Passing them as arguments is not offered: argv is readable by any local user."
        )
    return lines[0], lines[1]


def _print_verbatim(text: str) -> None:
    """Print text exactly as it is, without rich reinterpreting it.

    Two things need this. A tool's own output: restic prints aligned tables
    that rich re-wraps into nonsense at any narrow terminal, and writes things
    like `filtered by []` where square brackets are rich's markup syntax, so
    arbitrary remote output can be swallowed or raise on its way to the screen.

    And anything the operator is meant to *copy*. A repository location is
    longer than eighty columns, and a line break inserted into the middle of
    one produces a broken paste into `pless.toml`. Both cases were found by a
    test rather than by reading.
    """
    console.print(text, markup=False, highlight=False, soft_wrap=True)


def _host(cfg: config.Config) -> targets.Host:
    try:
        return targets.resolve_host(cfg)
    except targets.TargetError as exc:
        _fail(str(exc))
        raise  # unreachable; helps the type checker


def _remote_df(cfg: config.Config, mount: str = "/") -> diskcheck.DiskSnapshot:
    target = _host(cfg)
    result = sshexec.run(target.ssh_args, f"df -Pk {mount}")
    if not result.ok:
        _fail(f"df over SSH failed against {target.label}: {result.stderr.strip()}")
    return diskcheck.parse_df_output(result.stdout)


@server_app.command("df")
def server_df() -> None:
    """Show disk usage on the active target."""
    cfg = config.load_config()
    target = _host(cfg)
    snapshot = _remote_df(cfg)
    console.print(
        f"{target.label}: {_human_size(snapshot.total_bytes)} total, "
        f"{_human_size(snapshot.used_bytes)} used ({snapshot.used_percent:.0f}%), "
        f"{_human_size(snapshot.avail_bytes)} free."
    )


@server_app.command("status")
def server_status() -> None:
    """Show whether the configured host is reachable."""
    cfg = config.load_config()
    target = _host(cfg)
    result = sshexec.run(target.ssh_args, "uptime")
    if result.ok:
        console.print(f"{target.label}: up — {result.stdout.strip()}")
    else:
        console.print(f"[red]✗[/red] {target.label}: unreachable over SSH")


@app.command()
def ssh() -> None:
    """Open an interactive SSH session on the active target."""
    cfg = config.load_config()
    target = _host(cfg)
    raise typer.Exit(subprocess.call(sshexec.ssh_command(target.ssh_args)))


@vm_app.command("create")
def vm_create(
    force: bool = typer.Option(
        False, "--force", help="Repoint [host] even if it names a different machine."
    ),
) -> None:
    """Create a local VM and point [host] at it."""
    cfg = config.load_config()
    try:
        vm.require_backend(cfg.vm.backend)
    except vm.VmError as exc:
        _fail(str(exc))

    if not vm.exists(cfg.vm):
        user_data_path: Path | None = None
        if cfg.vm.backend == "multipass":
            # Multipass provisions with cloud-init, the way a cloud server does.
            try:
                pubkey = hostspec.read_pubkey(cfg.host.key)
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
    else:
        console.print(f"[yellow]•[/yellow] VM {cfg.vm.name!r} already exists.")

    # The command that made the machine knows how to reach it, so it writes
    # [host] rather than asking anyone to copy a path by hand.
    try:
        ssh_config, alias = vm.ssh_config_for(cfg.vm, cfg.host.key)
        hostfile.apply(
            config.find_config_file() or Path("pless.toml"),
            hostfile.HostUpdate(ssh_config=str(ssh_config), ssh_alias=alias),
            force=force,
        )
    except (vm.VmError, hostfile.HostFileError) as exc:
        _fail(str(exc))
        return

    console.print(f"[green]✓[/green] {cfg.vm.name} is up, and \\[host] now points at {alias}.")
    next_step = "pless bootstrap" if cfg.vm.backend == "lima" else "pless doctor"
    console.print(f"Next: [bold]{next_step}[/bold]")


@vm_app.command("destroy")
def vm_destroy(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm deleting the VM."),
    force: bool = typer.Option(
        False, "--force", help="Delete even if the encrypted volume is unlocked."
    ),
) -> None:
    """Delete the VM and everything on it. Destructive."""
    cfg = config.load_config()
    if not confirm:
        _fail(f"This deletes VM {cfg.vm.name!r} and everything on it. Run again with --confirm.")

    # A local VM can be someone's real archive, and `vm destroy` is a command
    # muscle memory types often. An unlocked volume means data is live in it.
    if not force:
        try:
            state = storage.status(cfg, _host(cfg))
            if state.is_mounted:
                _fail(
                    "The encrypted volume is unlocked and mounted, which means this VM is "
                    "holding live data. Run `pless lock` first — or pass --force if you are "
                    "certain there is nothing here you want."
                )
        except (targets.TargetError, storage.StorageError):
            pass  # unreachable or never set up: nothing to protect

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
    target = _host(cfg)
    result = sshexec.run(target.ssh_args, "sh -s", timeout=120, input_text=audit.COLLECT_SCRIPT)
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
    target = _host(cfg)
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
    target = _host(cfg)

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
    target = _host(cfg)
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
    target = _host(cfg)

    reachable = sshexec.run(target.ssh_args, "true").ok

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
        result = sshexec.run(target.ssh_args, "sh -s", timeout=120, input_text=audit.COLLECT_SCRIPT)
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
        backup_configured=cfg.backup.is_configured,
        # A real answer at last, read from the record `pless backup verify`
        # leaves on the target. Missing, failed, unreadable and stale all count
        # as "no", because silence here would read as approval.
        backup_verified=(
            reachable and cfg.backup.is_configured and backup.is_verified(cfg, target)
        ),
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
    target = _host(cfg)
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
    generate: bool = typer.Option(
        False,
        "--generate",
        help="Generate the passphrase in the documented format instead of choosing one.",
    ),
) -> None:
    """Format the data volume as LUKS2 with ext4. Destructive."""
    cfg = config.load_config()
    target = _host(cfg)
    if not confirm:
        _fail("This FORMATS the data volume on the target. Run again with --confirm.")

    if generate:
        passphrase = secretgen.human_passphrase()
        # Shown once, before the volume exists. There is no second chance to
        # print it, and no copy anywhere else.
        console.print("\n[bold]Your LUKS passphrase — this is the only time it is shown:[/bold]\n")
        console.print(f"    [bold cyan]{passphrase}[/bold cyan]\n")
        console.print(
            "[bold yellow]! Put it in your password manager before continuing. "
            "Nothing else has a copy.[/bold yellow]"
        )
        if not typer.confirm("Saved it?", default=False):
            _fail("Nothing was formatted. Run again when you are ready to save it.")
    else:
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
    target = _host(cfg)
    try:
        console.print(_storage_status_line(storage.status(cfg, target)))
    except storage.StorageError as exc:
        _fail(str(exc))


@app.command()
def unlock() -> None:
    """Unlock and mount the data volume after a reboot, then start the stack."""
    cfg = config.load_config()
    target = _host(cfg)
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
    target = _host(cfg)
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
    target = _host(cfg)
    console.print(f"Deploying to {target.label}. The first run pulls ~2 GB of images…")
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
    target = _host(cfg)
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
    target = _host(cfg)
    try:
        console.print(deploy.compose(target, f"logs --tail {tail} {service}".strip(), timeout=60))
    except deploy.DeployError as exc:
        _fail(str(exc))


@paperless_app.command("health")
def paperless_health() -> None:
    """Check that Paperless answers on the target's localhost."""
    cfg = config.load_config()
    target = _host(cfg)
    try:
        code = deploy.http_status(target)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return
    if code in ("200", "302"):
        console.print(f"[green]✓[/green] Paperless is answering (HTTP {code}).")
    else:
        _fail(f"Paperless is not answering as expected (HTTP {code}). See `pless deploy logs`.")


@b2_app.command("provision")
def b2_provision(
    bucket: str = typer.Option(..., "--bucket", help="Bucket name. Globally unique across B2."),
    new_key: bool = typer.Option(
        False, "--new-key", help="Mint another machine key even if one already exists."
    ),
) -> None:
    """Create a bucket Object Lock protects, and mint a key restricted to it."""
    cfg = config.load_config()
    days = cfg.backup.version_retention_days

    console.print(
        f"This creates the bucket [bold]{bucket}[/bold] with Object Lock in governance mode, "
        f"retaining versions for [bold]{days} days[/bold], and mints a machine key restricted "
        "to it."
    )
    console.print(
        "The provisioning credential is used for the API calls and written nowhere — not "
        "\\[backup], not .env, not argv."
    )

    # Never an option: anything in argv is readable by any local user through
    # `ps`, and this credential can create buckets and mint keys.
    try:
        key_id, application_key = _read_credential_pair(
            "Provisioning keyID", "Provisioning applicationKey"
        )
    except _NoCredentialInput as exc:
        _fail(str(exc))
        return

    try:
        outcome = b2.provision(
            b2.ProvisioningCredential(key_id=key_id, application_key=application_key),
            bucket,
            days,
            b2.http_transport(),
            allow_new_key=new_key,
            progress=lambda message: console.print(f"  {message}"),
        )
    except b2.B2Error as exc:
        _fail_with_remedy(exc)
        return

    verb = "Created" if outcome.created_bucket else "Adopted"
    headline = f"\n[green]✓[/green] {verb} [bold]{outcome.bucket.bucket_name}[/bold]"
    period = outcome.bucket.lock.period
    if period:
        headline += (
            f" — Object Lock on, governance mode, {period.duration} {period.unit} of retention."
        )
    console.print(headline)
    if outcome.repaired_retention and not outcome.created_bucket:
        console.print("[green]✓[/green] The retention was missing or too short, and was set.")

    console.print("\n[bold]Put this in pless.toml, under \\[backup]:[/bold]\n")
    _print_verbatim(f'    restic_repository = "{outcome.repository}"')

    console.print("\n[bold]The machine key — this is the only time it is shown:[/bold]\n")
    _print_verbatim(f"    B2_KEY_ID={outcome.machine_key.key_id}")
    _print_verbatim(f"    B2_APPLICATION_KEY={outcome.machine_key.application_key}\n")
    console.print(
        "[bold yellow]! Put it in your password manager now. pless keeps no copy, and "
        "Backblaze will not show it again.[/bold yellow]"
    )
    console.print(
        f"[green]✓[/green] It holds exactly: {', '.join(outcome.machine_key.capabilities)} — "
        "and not bypassGovernance, which is what stops a compromised machine destroying "
        "history."
    )

    for note in outcome.notes:
        console.print(f"[yellow]•[/yellow] {note}")

    console.print("\nNext: [bold]pless backup init[/bold]")


@backup_app.command("init")
def backup_init() -> None:
    """Write the backup script, unit and timer to the target, and enable them."""
    cfg = config.load_config()
    sec = config.load_secrets()
    target = _host(cfg)
    try:
        backup.install(cfg, sec, target)
        created = backup.initialise_repository(target)
    except (backup.BackupError, storage.StorageError) as exc:
        _fail(str(exc))
        return

    kind = cfg.backup.repository_kind
    if created:
        console.print(f"[green]✓[/green] Initialised a new restic repository ({kind}).")
    else:
        console.print(f"[green]✓[/green] Using the existing restic repository ({kind}).")
    console.print(
        f"[green]✓[/green] Timer enabled: backups run [bold]{cfg.backup.schedule}[/bold]."
    )
    console.print(
        "[bold yellow]! RESTIC_PASSWORD has no recovery path. Without it this repository "
        "is an encrypted blob nobody can open, including you. Save it in your password "
        "manager now.[/bold yellow]"
    )
    if cfg.backup.is_local_repository:
        console.print(
            "[yellow]•[/yellow] This is a local repository. It protects against deletion "
            "and corruption, and not at all against losing the machine — which is the "
            "thing backup exists for."
        )


@backup_app.command("run")
def backup_run() -> None:
    """Run a backup now: quiesce, dump the database, export, and snapshot."""
    cfg = config.load_config()
    target = _host(cfg)
    console.print("Running the backup. A first snapshot can take hours…")
    try:
        record = backup.run(target)
    except backup.BackupError as exc:
        _fail(str(exc))
        return

    match record.outcome:
        case backup.RunOutcome.SKIPPED_LOCKED:
            console.print(f"[yellow]•[/yellow] Skipped: {record.detail}")
        case backup.RunOutcome.SUCCEEDED:
            console.print(
                f"[green bold]✓ {record.documents_exported} documents in snapshot "
                f"{record.snapshot_id[:8]}.[/green bold]"
            )
            if record.queue_moved_during_run:
                console.print(
                    "[yellow]•[/yellow] Documents were consumed while the export ran, so the "
                    "snapshot may not include the very latest. The next run picks them up."
                )
        case _:
            _fail(
                f"The backup failed: {record.detail} "
                "See `journalctl -u pless-backup.service` on the target."
            )


@backup_app.command("export")
def backup_export() -> None:
    """Run Paperless's document exporter on the target, without a snapshot."""
    cfg = config.load_config()
    target = _host(cfg)
    console.print("Exporting documents. Nothing must be consuming while this runs…")
    try:
        backup.export_only(cfg, target)
    except backup.BackupError as exc:
        _fail(str(exc))
        return
    console.print(
        f"[green]✓[/green] Exported to {composegen.INSTALL_DIR}/export on the target — "
        "on the encrypted volume, not in a snapshot."
    )


@backup_app.command("verify")
def backup_verify(
    level: str = typer.Option(
        "content",
        "--level",
        help="content: restore a sample and compare. full: build a machine and restore into it.",
    ),
    snapshot: str = typer.Option(
        "latest", "--snapshot", help="Which snapshot a full rehearsal restores. Default: newest."
    ),
) -> None:
    """Prove the documents come back, by restoring rather than by inspecting."""
    cfg = config.load_config()
    target = _host(cfg)

    if level not in ("content", "full"):
        _fail(f"Unknown level {level!r}. Use 'content' or 'full'.")

    if level == "full":
        _backup_verify_full(cfg, target, snapshot)
        return

    console.print("Verifying: restoring a sample from the newest snapshot…")
    try:
        record = backup.verify(target)
    except backup.BackupError as exc:
        _fail(str(exc))
        return

    if record.passed:
        console.print(f"[green bold]✓ {record.detail}[/green bold]")
    else:
        console.print(f"[red]✗ {record.detail}[/red]")
        raise typer.Exit(code=1)


def _backup_verify_full(cfg: config.Config, target: targets.Host, snapshot: str) -> None:
    """The rehearsal, which costs a VM and tens of minutes — so it says so first."""
    sec = config.load_secrets()
    console.print(
        f"[bold]A full rehearsal builds {drill.drill_vm_config(cfg).name} from nothing[/bold] — "
        "bootstrap, encrypted volume, Paperless, then the restore — and destroys it "
        "afterwards. Expect tens of minutes, and a few gigabytes of downloads."
    )
    try:
        result = drill.verify_full(
            cfg, sec, target, snapshot, progress=lambda message: console.print(f"  {message}")
        )
    except drill.DrillError as exc:
        _fail(str(exc))
        return

    if not result.vm_destroyed:
        console.print(
            f"[yellow]•[/yellow] {result.vm_name} was left running, because a rehearsal that "
            "fails is the one worth looking at. Remove it when you are done with it."
        )
    if result.record.passed:
        console.print(f"[green bold]✓ {result.record.detail}[/green bold]")
    else:
        console.print(f"[red]✗ {result.record.detail}[/red]")
        raise typer.Exit(code=1)


@backup_app.command("restore")
def backup_restore(
    snapshot: str = typer.Option(
        "latest", "--snapshot", help="Snapshot id to restore. Default: the newest."
    ),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm restoring into this target."),
) -> None:
    """Restore a snapshot into a fresh installation, and import the documents."""
    cfg = config.load_config()
    target = _host(cfg)

    if not confirm:
        _fail(
            f"This restores the archive into {target.label} and imports it into Paperless. "
            "It is meant for a fresh installation, and it refuses one that already holds "
            "documents. Run again with --confirm."
        )

    typed = typer.prompt(f"Type the host label ({target.label}) to confirm")
    if typed != target.label:
        _fail("The label did not match — aborting.")

    console.print("Restoring. A large archive takes a long time to import…")
    try:
        imported = backup.restore(cfg, target, snapshot)
    except backup.BackupError as exc:
        _fail(str(exc))
        return

    console.print(f"[green bold]✓ {imported} documents restored into {target.label}.[/green bold]")
    console.print(
        "Next: [bold]pless backup init[/bold], so this machine backs up in its own right. "
        "A restored machine that never takes a snapshot is one failure from being where "
        "you started."
    )


@backup_app.command("extract")
def backup_extract(
    snapshot: str = typer.Option(
        "latest", "--snapshot", help="Snapshot to extract from. Default: the newest."
    ),
    to: Path | None = typer.Option(
        None, "--to", help="Where to write the documents. Default: [paths] local_backups."
    ),
    confirm: bool = typer.Option(
        False, "--confirm", help="Confirm writing readable documents to this disk."
    ),
) -> None:
    """Pull the documents out of a snapshot in the clear, onto this machine."""
    cfg = config.load_config()
    target = _host(cfg)
    destination = (to or cfg.paths.backups).expanduser().resolve()

    if not confirm:
        _fail(
            f"This writes readable documents to {destination}, on an unencrypted disk, "
            "where nothing protects them but the permissions of this machine. It is an "
            "escape hatch and an inspection tool, never a backup layer — see ADR 0018. "
            "Run again with --confirm."
        )

    console.print(f"Extracting from snapshot {snapshot} into {destination}…")
    try:
        written = backup.extract(cfg, target, destination, snapshot)
    except backup.BackupError as exc:
        _fail(str(exc))
        return

    console.print(f"[green bold]✓ Documents written to {written}.[/green bold]")
    console.print(
        "[yellow]•[/yellow] These are readable files, not a re-importable export — "
        "`pless backup restore` is what puts an archive back. Delete them when you are "
        "done with them."
    )


@backup_app.command("forget")
def backup_forget(
    prune: bool = typer.Option(
        False, "--prune", help="Actually remove snapshots and reclaim space."
    ),
    confirm: bool = typer.Option(False, "--confirm", help="Required with --prune."),
) -> None:
    """Apply the retention policy. Shows what would go unless given --prune."""
    cfg = config.load_config()
    target = _host(cfg)

    if not cfg.backup.is_configured:
        _fail("[backup] restic_repository is empty, so there is no history to thin out.")

    if not prune:
        console.print("Dry run — nothing will be removed.")
        try:
            output = backup.forget(cfg, target, dry_run=True)
        except backup.BackupError as exc:
            _fail(str(exc))
            return
        _print_verbatim(output or "Nothing matched the retention policy.")
        console.print("[yellow]•[/yellow] Run with [bold]--prune --confirm[/bold] to apply it.")
        return

    if not confirm:
        _fail("--prune removes snapshots permanently. Run again with --confirm.")

    label = backup.repository_label(cfg.backup.restic_repository)
    typed = typer.prompt(f"Type the repository name ({label}) to confirm")
    if typed != label:
        _fail("The name did not match — aborting.")

    console.print("Applying retention and pruning. This can take a while…")
    try:
        output = backup.forget(cfg, target, dry_run=False)
    except backup.BackupError as exc:
        _fail(str(exc))
        return
    _print_verbatim(output)
    console.print("[green bold]✓ Retention applied.[/green bold]")
    # Only where Object Lock can apply at all. On a local repository prune
    # really does reclaim space, and a drill found this hint firing there and
    # telling the operator something untrue. Even off-site pless cannot know
    # whether the bucket carries a lock — that is the check #16 is about — so
    # the sentence stays conditional.
    if cfg.backup.version_retention_days and not cfg.backup.is_local_repository:
        console.print(
            f"[yellow]•[/yellow] If the bucket carries Object Lock, deletes become delete "
            f"markers and prune reclaims nothing until the "
            f"{cfg.backup.version_retention_days}-day retention expires. That is the price "
            "of immutability, not a failure."
        )


@backup_app.command("status")
def backup_status(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Repository, last run, snapshots and timer state."""
    cfg = config.load_config()
    target = _host(cfg)

    if not cfg.backup.is_configured:
        _fail(
            "[backup] restic_repository is empty, so nothing is being backed up. "
            "Set it in pless.toml and run `pless backup init`."
        )

    try:
        record = backup.read_run_record(target)
        verification = backup.read_verification_record(target)
        snaps = backup.snapshots(target)
        timer = backup.timer_state(target)
    except backup.BackupError as exc:
        _fail(str(exc))
        return

    latest = backup.latest_snapshot(snaps)
    # A record on the target is only as trustworthy as the target, but it names
    # a snapshot — so the claim is checkable against the repository rather than
    # taken on trust (ADR 0019).
    known_ids = {s.id for s in snaps}
    verified_snapshot_missing = bool(
        verification and verification.snapshot_id and verification.snapshot_id not in known_ids
    )
    if json_output:
        console.print_json(
            data={
                "repository_kind": cfg.backup.repository_kind,
                "timer": timer,
                "snapshots": len(snaps),
                "latest_snapshot": latest.id if latest else None,
                "latest_snapshot_time": latest.time if latest else None,
                "last_run": {
                    "outcome": str(record.outcome),
                    "finished_at": record.finished_at,
                    "documents_exported": record.documents_exported,
                    "snapshot_id": record.snapshot_id,
                    "queue_moved_during_run": record.queue_moved_during_run,
                }
                if record
                else None,
                "verification": {
                    "level": str(verification.level),
                    "performed_at": verification.performed_at,
                    "passed": verification.passed,
                    "snapshot_id": verification.snapshot_id,
                    "documents_found": verification.documents_found,
                    "sample_size": verification.sample_size,
                    "snapshot_still_present": not verified_snapshot_missing,
                }
                if verification
                else None,
            }
        )
        return

    console.print(f"Repository: {cfg.backup.repository_kind}")
    console.print(f"Timer: {timer} ({cfg.backup.schedule})")
    console.print(f"Snapshots: {len(snaps)}")
    if latest:
        console.print(f"Latest: {latest.short_id} at {latest.time}")
    else:
        console.print(
            "[red]✗[/red] No snapshots tagged pless. Either nothing has been backed up "
            "yet, or the repository was recreated — the second looks like success and "
            "contains nothing."
        )
    if record is None:
        console.print("[yellow]•[/yellow] No run has been recorded on the target yet.")
    elif record.succeeded:
        console.print(
            f"[green]✓[/green] Last run {record.finished_at}: "
            f"{record.documents_exported} documents, snapshot {record.snapshot_id[:8]}."
        )
    else:
        console.print(
            f"[red]✗[/red] Last run {record.finished_at or '(unfinished)'}: "
            f"{record.outcome} — {record.detail}"
        )

    if verification is None:
        console.print(
            "[yellow]•[/yellow] Never verified. A backup that has not been restored is a "
            "belief — run `pless backup verify`."
        )
    else:
        mark = "[green]✓[/green]" if verification.passed else "[red]✗[/red]"
        console.print(f"{mark} Verified {verification.performed_at}: {verification.detail}")
        if verification.passed and verification.is_stale(
            cfg.backup.verify_max_age_days, datetime.now(UTC)
        ):
            console.print(
                f"[yellow]•[/yellow] That is older than {cfg.backup.verify_max_age_days} days, "
                "so `pless preflight` no longer counts it."
            )
    if verified_snapshot_missing and verification:
        console.print(
            f"[red]✗[/red] The verification record names snapshot "
            f"{verification.snapshot_id[:8]}, which is not in the repository. Either it was "
            "pruned since, or the record does not describe this repository."
        )


@app.command()
def tunnel() -> None:
    """Open an SSH tunnel to Paperless at http://localhost:8000. Ctrl-C closes it."""
    cfg = config.load_config()
    target = _host(cfg)
    console.print(
        f"Tunnel open: [bold]http://localhost:{composegen.WEB_PORT}[/bold] "
        f"-> {target.label}. Ctrl-C to close."
    )
    raise typer.Exit(
        subprocess.call(
            sshexec.ssh_command(target.tunnel_args(composegen.WEB_PORT, composegen.WEB_PORT))
        )
    )


if __name__ == "__main__":
    app()
