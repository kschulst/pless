"""pless — CLI for provisjonering og drift av Paperless-ngx på Hetzner Cloud."""

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
    sshexec,
    storage,
    tailscale,
    targets,
    vm,
)

app = typer.Typer(
    name="pless",
    help="Provisjonering og dag-2-drift av selvhostet Paperless-ngx (Pi, VM eller Hetzner).",
    no_args_is_help=True,
)
hetzner_app = typer.Typer(help="Hetzner Cloud-oppsett og sjekker.", no_args_is_help=True)
docs_app = typer.Typer(help="Skanning og estimering av lokale dokumenter.", no_args_is_help=True)
server_app = typer.Typer(help="Serverstatus og -operasjoner.", no_args_is_help=True)
vm_app = typer.Typer(help="Lokal dev-VM via Multipass.", no_args_is_help=True)
storage_app = typer.Typer(help="LUKS-kryptert datalagring på target.", no_args_is_help=True)
deploy_app = typer.Typer(help="Deploy og drift av Paperless-stacken.", no_args_is_help=True)
paperless_app = typer.Typer(help="Paperless-app-operasjoner.", no_args_is_help=True)
tailscale_app = typer.Typer(help="Tailscale-tilgang til targetet.", no_args_is_help=True)
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
    """Vis pless-versjon."""
    console.print(f"pless {__version__}")


@app.command()
def init(
    with_secrets: bool = typer.Option(
        False, "--secrets", help="Generer sterke secrets og skriv dem til .env."
    ),
) -> None:
    """Opprett .env fra .env.example og verifiser at pless.toml finnes."""
    if config.find_config_file() is None:
        _fail("Fant ingen pless.toml — kjør fra prosjektmappa (den ligger i repoet).")

    env_path = Path(".env")
    example_path = Path(".env.example")
    if env_path.exists():
        console.print("[yellow]•[/yellow] .env finnes allerede — rører den ikke.")
    elif example_path.exists():
        shutil.copy(example_path, env_path)
        console.print("[green]✓[/green] Opprettet .env fra .env.example.")
    else:
        _fail("Fant verken .env eller .env.example.")

    if with_secrets:
        content = env_path.read_text()
        generated: list[str] = []
        for key in ("PAPERLESS_ADMIN_PASSWORD", "PAPERLESS_SECRET_KEY", "POSTGRES_PASSWORD"):
            if f"{key}=\n" in content or content.rstrip().endswith(f"{key}="):
                content = content.replace(f"{key}=", f"{key}={pysecrets.token_urlsafe(32)}", 1)
                generated.append(key)
        env_path.write_text(content)
        if generated:
            console.print(f"[green]✓[/green] Genererte secrets: {', '.join(generated)}")
            console.print(
                "[bold yellow]! Lagre disse i Bitwarden NÅ — "
                ".env er kun en lokal cache.[/bold yellow]"
            )
        else:
            console.print(
                "[yellow]•[/yellow] Alle secrets var allerede satt — genererte ingenting."
            )

    cfg = config.load_config()
    if cfg.target.type == "hetzner":
        console.print(
            "\nNeste steg: fyll inn HCLOUD_TOKEN i .env, kjør deretter [bold]pless doctor[/bold]."
        )
    else:
        console.print("\nNeste steg: kjør [bold]pless doctor[/bold].")


@app.command()
def doctor() -> None:
    """Sjekk lokalt miljø: verktøy, konfig, nøkler og token."""
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

    check(sys.version_info >= (3, 12), f"Python {sys.version.split()[0]} (krever 3.12+)")
    check(shutil.which("ssh") is not None, "ssh finnes i PATH")
    check(shutil.which("uv") is not None, "uv finnes i PATH", "https://docs.astral.sh/uv/")
    check(config.find_config_file() is not None, "pless.toml funnet")
    check(Path(".env").exists(), ".env finnes", "kjør `pless init`")
    check(
        cfg.ssh.key.exists(),
        f"SSH-nøkkel finnes ({cfg.ssh.key})",
        "generer med ssh-keygen -t ed25519",
    )

    console.print(f"\nAktivt target: [bold]{cfg.target.type}[/bold]")
    if cfg.target.type == "vm":
        tool = "limactl" if cfg.vm.backend == "lima" else "multipass"
        hint = (
            "kjør `brew install lima`"
            if cfg.vm.backend == "lima"
            else "kjør `! brew install --cask multipass --yes`"
        )
        check(shutil.which(tool) is not None, f"{tool} er installert ({cfg.vm.backend})", hint)
    elif cfg.target.type == "pi":
        check(bool(cfg.pi.host), "[pi] host er satt i pless.toml")
        if cfg.pi.data_mode == "partition":
            check(
                bool(cfg.pi.data_device),
                "[pi] data_device er satt (kreves av data_mode=partition)",
            )
        else:
            console.print(
                f"[green]✓[/green] [pi] data_mode=file ({cfg.pi.data_size_gb} GB LUKS-fil)"
            )
    elif cfg.target.type == "hetzner":
        check(bool(sec.hcloud_token), "HCLOUD_TOKEN er satt", "legg i .env, kilde: Bitwarden")

    if cfg.access.mode == "tailscale" and cfg.target.type != "vm":
        warn(
            shutil.which("tailscale") is not None,
            "tailscale-CLI finnes lokalt",
            "trengs først ved deploy — https://tailscale.com/download",
        )
    warn(
        bool(sec.paperless_admin_password),
        "Paperless-secrets generert",
        "kjør `pless init --secrets`",
    )

    if problems:
        _fail(f"{problems} problem(er) må fikses.")
    next_step = "pless vm create" if cfg.target.type == "vm" else "pless server status"
    console.print(f"\n[green bold]Alt klart.[/green bold] Neste: [bold]{next_step}[/bold]")


@hetzner_app.command("check-token")
def hetzner_check_token() -> None:
    """Verifiser HCLOUD_TOKEN mot Hetzner API med read-kall."""
    sec = config.load_secrets()
    cfg = config.load_config()
    try:
        client = hetzner.make_client(sec.hcloud_token)
        info = hetzner.check_token(client)
    except Exception as exc:  # hcloud kaster provider-spesifikke exceptions
        _fail(f"Token-sjekk feilet: {exc}")
        return

    console.print("[green]✓[/green] Token er gyldig.")
    console.print(f"  Servere i prosjektet: {info.server_count} {info.server_names or ''}")
    console.print(f"  Lokasjoner tilgjengelig: {', '.join(info.locations)}")
    if cfg.hetzner.location not in info.locations:
        console.print(
            f"[yellow]•[/yellow] Konfigurert lokasjon {cfg.hetzner.location} ikke i lista!"
        )


@docs_app.command("scan")
def docs_scan(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Mappe som skal skannes."),
    hashes: bool = typer.Option(False, "--hashes", help="Beregn sha256 og rapporter duplikater."),
) -> None:
    """Skann en lokal mappe: klassifiser filer og finn Evernote-rester."""
    result = docscan.scan(path, with_hashes=hashes)

    table = Table(title=f"Skann av {path}")
    table.add_column("Kategori")
    table.add_column("Filer", justify="right")
    table.add_column("Størrelse", justify="right")
    table.add_row(
        "Klar for Paperless", str(len(result.supported)), _human_size(result.supported_bytes)
    )
    table.add_row(
        "Trenger konvertering (.enex/.html)",
        str(len(result.needs_conversion)),
        _human_size(result.needs_conversion_bytes),
    )
    table.add_row("Ustøttet/ignorert", str(len(result.unsupported)), "—")
    console.print(table)

    top = result.by_extension.most_common(10)
    console.print("Vanligste filtyper: " + ", ".join(f"{ext} ({n})" for ext, n in top))

    if result.needs_conversion:
        console.print(
            "\n[yellow]•[/yellow] Evernote/HTML-filer funnet — disse må konverteres før import."
            " Konverteringssteget scopes når vi ser hva skannen viser."
        )
    if hashes:
        dupes = result.duplicate_groups()
        if dupes:
            dup_files = sum(len(v) - 1 for v in dupes.values())
            console.print(
                f"[yellow]•[/yellow] {dup_files} duplikatfil(er) i {len(dupes)} grupper "
                "(identisk innhold). Paperless avviser duplikater selv, men dette er antallet."
            )
        else:
            console.print("[green]✓[/green] Ingen innholdsduplikater.")


@docs_app.command("estimate")
def docs_estimate(
    path: Path = typer.Argument(..., exists=True, file_okay=False),
    remote: bool = typer.Option(
        True,
        "--remote/--local-only",
        help="Sjekk faktisk disk på serveren via SSH (krever at serveren finnes).",
    ),
) -> None:
    """Estimer diskbehov etter ingestion og sammenlign med serverens disk."""
    cfg = config.load_config()
    result = docscan.scan(path)
    upload = result.supported_bytes + result.needs_conversion_bytes

    console.print(f"Opplastingsvolum (inkl. ukonvertert): {_human_size(upload)}")
    console.print(
        f"Estimert vekst på server (x{diskcheck.INGEST_GROWTH_FACTOR} ingest "
        f"+ x{diskcheck.EXPORT_COPY_FACTOR} eksportkopi): "
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
        f"Server-disk: {_human_size(snapshot.total_bytes)} totalt, "
        f"{snapshot.used_percent:.0f}% brukt nå → "
        f"{projection.projected_used_percent:.0f}% etter import, "
        f"{projection.projected_free_gb:.1f} GB ledig."
    )
    match projection.recommendation:
        case diskcheck.Recommendation.PROCEED:
            console.print("[green bold]✓ Trygt å fortsette.[/green bold]")
        case diskcheck.Recommendation.REDUCE_BATCH:
            console.print(
                "[yellow bold]! Over terskel — importér i mindre batcher og rydd "
                "eksportkopier underveis, eller utvid lagringen.[/yellow bold]"
            )
        case diskcheck.Recommendation.ADD_STORAGE:
            console.print(
                "[red bold]✗ Disken er for liten for dette volumet — legg til Hetzner "
                "Volume eller velg større servertype.[/red bold]"
            )


def _resolve_target(cfg: config.Config) -> targets.TargetHost:
    try:
        return targets.resolve_target(cfg, config.load_secrets())
    except (targets.TargetError, ValueError) as exc:
        _fail(str(exc))
        raise  # unreachable; hjelper typesjekk


def _remote_df(cfg: config.Config, mount: str = "/") -> diskcheck.DiskSnapshot:
    target = _resolve_target(cfg)
    result = sshexec.run(target.user, target.host, target.key, f"df -Pk {mount}", port=target.port)
    if not result.ok:
        _fail(f"SSH/df feilet mot {target.host}: {result.stderr.strip()}")
    return diskcheck.parse_df_output(result.stdout)


@server_app.command("df")
def server_df() -> None:
    """Vis diskbruk på aktivt target (df på rotfilsystemet via SSH)."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    snapshot = _remote_df(cfg)
    console.print(
        f"{target.name} ({target.host}): {_human_size(snapshot.total_bytes)} totalt, "
        f"{_human_size(snapshot.used_bytes)} brukt ({snapshot.used_percent:.0f}%), "
        f"{_human_size(snapshot.avail_bytes)} ledig."
    )


@server_app.command("status")
def server_status() -> None:
    """Vis status for aktivt target."""
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
            console.print(
                f"[yellow]•[/yellow] Ingen server ved navn {cfg.hetzner.server_name!r} ennå."
            )
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
        console.print(f"{target.name} ({target.host}): oppe — {result.stdout.strip()}")
    else:
        console.print(f"[red]✗[/red] {target.name} ({target.host}): utilgjengelig over SSH")


@app.command()
def ssh() -> None:
    """Åpne interaktiv SSH-sesjon mot aktivt target."""
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
    """Opprett dev-VM-en. lima → Debian 13 + bootstrap, multipass → Ubuntu + cloud-init."""
    cfg = config.load_config()
    try:
        vm.require_backend(cfg.vm.backend)
    except vm.VmError as exc:
        _fail(str(exc))
    if vm.exists(cfg.vm):
        console.print(f"[yellow]•[/yellow] VM-en {cfg.vm.name!r} finnes allerede.")
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
        f"Oppretter {cfg.vm.name} via {cfg.vm.backend} "
        f"({cfg.vm.cpus} vCPU, {cfg.vm.memory}, {cfg.vm.disk}) — "
        "første gang lastes imaget ned, dette kan ta noen minutter …"
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
        f"[green]✓[/green] {cfg.vm.name} er oppe: {', '.join(data['addresses'])}. "
        f"Neste: [bold]{next_step}[/bold]"
    )


@vm_app.command("destroy")
def vm_destroy(
    confirm: bool = typer.Option(False, "--confirm", help="Bekreft sletting av VM-en."),
) -> None:
    """Slett dev-VM-en og alt innhold. DESTRUKTIVT."""
    cfg = config.load_config()
    if not confirm:
        _fail(f"Dette sletter VM-en {cfg.vm.name!r} og ALT innhold. Kjør igjen med --confirm.")
    typed = typer.prompt(f"Skriv VM-navnet ({cfg.vm.name}) for å bekrefte")
    if typed != cfg.vm.name:
        _fail("Navnet stemte ikke — avbryter.")
    try:
        vm.delete(cfg.vm)
    except vm.VmError as exc:
        _fail(str(exc))
    console.print(f"[green]✓[/green] {cfg.vm.name} er slettet.")


@app.command("audit")
def audit_cmd(
    json_output: bool = typer.Option(False, "--json", help="Maskinlesbar output."),
) -> None:
    """Revider eksponering: hva kan noen på LAN-et ditt faktisk nå?"""
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
        _fail(f"Innsamling feilet: {result.stderr.strip()}")
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
    """Vis Tailscale-status på targetet."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    state = tailscale.status(target)
    if not state.installed:
        console.print(
            "[yellow]•[/yellow] Tailscale er ikke installert — kjør `pless tailscale up`."
        )
        return
    mark = "[green]✓[/green]" if state.is_up else "[red]✗[/red]"
    console.print(f"{mark} {state.backend_state} — {state.hostname or '(uten navn)'}")
    console.print(f"  Adresser: {', '.join(state.addresses) or '-'}")


@tailscale_app.command("up")
def tailscale_up() -> None:
    """Installer Tailscale og meld targetet inn i tailnetet (velges av TS_AUTHKEY)."""
    cfg = config.load_config()
    sec = config.load_secrets()
    target = _resolve_target(cfg)

    state = tailscale.status(target)
    if not state.installed:
        console.print("Installerer Tailscale …")
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
        f"[green]✓[/green] Med i tailnetet som [bold]{state.hostname}[/bold] "
        f"({', '.join(state.addresses)})."
    )
    console.print("Neste: [bold]pless harden[/bold] for å stenge SSH mot LAN-et.")


@app.command("harden")
def harden_cmd(
    confirm: bool = typer.Option(False, "--confirm", help="Bekreft at LAN-SSH stenges."),
) -> None:
    """Steng SSH mot LAN — kun tailnetet slipper inn etterpå."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    if not confirm:
        _fail(
            "Dette stenger SSH for alt annet enn Tailscale. Mister du tailnet-tilgang, "
            "kommer du bare inn med skjerm og tastatur på maskinen. Kjør igjen med --confirm."
        )
    try:
        tailscale.harden(target)
    except tailscale.TailscaleError as exc:
        _fail(str(exc))
        return
    console.print(
        "[green]✓[/green] SSH slipper nå kun inn via tailscale0. "
        "Verifiser med [bold]pless audit[/bold]."
    )


@app.command("bootstrap")
def bootstrap_cmd() -> None:
    """Applisér host-spec over SSH på et target som allerede er booted (typisk Pi)."""
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
    console.print("Installerer Docker, UFW, fail2ban og auto-oppdateringer — tar noen minutter …")
    try:
        bootstrap.apply(cfg, target)
    except bootstrap.BootstrapError as exc:
        _fail(str(exc))
        return
    console.print(
        "[green]✓[/green] Host-spec applisert. Neste: [bold]pless storage init --confirm[/bold]"
    )


def _storage_status_line(state: storage.StorageStatus) -> str:
    def mark(ok: bool) -> str:
        return "[green]✓[/green]" if ok else "[red]✗[/red]"

    return (
        f"{state.device}: LUKS {mark(state.is_luks)}  "
        f"åpen {mark(state.is_open)}  montert på {storage.MOUNTPOINT} {mark(state.is_mounted)}"
    )


@storage_app.command("init")
def storage_init(
    confirm: bool = typer.Option(False, "--confirm", help="Bekreft formatering av datadisken."),
) -> None:
    """Formater datadisken som LUKS2 + ext4. DESTRUKTIVT for disken."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    if not confirm:
        _fail("Dette FORMATERER datadisken på targetet. Kjør igjen med --confirm.")
    passphrase = typer.prompt(
        "Velg LUKS-passphrase (lagre i Bitwarden FØR du fortsetter)",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        device = storage.init(cfg, target, passphrase)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] {device} er LUKS-formatert, ext4 og montert.")
    console.print(_storage_status_line(storage.status(cfg, target)))
    console.print(
        "[bold yellow]! Passphrasen finnes KUN i hodet ditt og Bitwarden — "
        "uten den er disken (med vilje) verdiløs.[/bold yellow]"
    )


@storage_app.command("status")
def storage_status_cmd() -> None:
    """Vis LUKS-status for datadisken på aktivt target."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(_storage_status_line(storage.status(cfg, target)))
    except storage.StorageError as exc:
        _fail(str(exc))


@app.command()
def unlock() -> None:
    """Lås opp og monter datadisken etter boot/strømbrudd; start stacken hvis deployet."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    passphrase = typer.prompt("LUKS-passphrase", hide_input=True)
    try:
        state = storage.unlock(cfg, target, passphrase)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] Låst opp. {_storage_status_line(state)}")


@app.command()
def lock() -> None:
    """Stopp stacken, avmonter og lås datadisken."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        state = storage.lock(cfg, target)
    except storage.StorageError as exc:
        _fail(str(exc))
        return
    console.print(f"[green]✓[/green] Låst. {_storage_status_line(state)}")


@deploy_app.command("paperless")
def deploy_paperless() -> None:
    """Legg ut compose-stack + systemd-unit på target og start Paperless."""
    cfg = config.load_config()
    sec = config.load_secrets()
    target = _resolve_target(cfg)
    console.print(f"Deployer til {target.name} ({target.host}) — første gang pulles ~2 GB images …")
    try:
        deploy.install(cfg, sec, target)
    except (deploy.DeployError, storage.StorageError, ValueError) as exc:
        _fail(str(exc))
        return
    console.print("[green]✓[/green] Stacken er startet. Venter på at webserveren svarer …")
    if deploy.wait_healthy(target):
        console.print(
            f"[green bold]✓ Paperless er oppe.[/green bold] "
            f"Kjør [bold]pless tunnel[/bold] og åpne http://localhost:{composegen.WEB_PORT} "
            f"(bruker: {cfg.paperless.admin_user}, passord: PAPERLESS_ADMIN_PASSWORD fra .env)."
        )
    else:
        console.print(
            "[yellow]•[/yellow] Webserveren svarer ikke ennå — første oppstart migrerer "
            "databasen og kan ta flere minutter. Følg med: [bold]pless deploy logs[/bold]"
        )


@deploy_app.command("status")
def deploy_status() -> None:
    """Vis containerstatus for stacken."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(deploy.compose(target, "ps --format table"))
    except deploy.DeployError as exc:
        _fail(str(exc))


@deploy_app.command("logs")
def deploy_logs(
    service: str = typer.Argument("", help="Tjeneste (webserver, db, broker, gotenberg, tika)."),
    tail: int = typer.Option(50, "--tail", help="Antall linjer."),
) -> None:
    """Vis logger fra stacken."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        console.print(deploy.compose(target, f"logs --tail {tail} {service}".strip(), timeout=60))
    except deploy.DeployError as exc:
        _fail(str(exc))


@paperless_app.command("health")
def paperless_health() -> None:
    """Sjekk at Paperless svarer på targetets localhost."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    try:
        code = deploy.http_status(target)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return
    if code in ("200", "302"):
        console.print(f"[green]✓[/green] Paperless svarer (HTTP {code}).")
    else:
        _fail(f"Paperless svarer ikke som forventet (HTTP {code}). Se `pless deploy logs`.")


@app.command()
def tunnel() -> None:
    """Åpne SSH-tunnel til Paperless (http://localhost:8000). Ctrl-C avslutter."""
    cfg = config.load_config()
    target = _resolve_target(cfg)
    console.print(
        f"Tunnel åpen: [bold]http://localhost:{composegen.WEB_PORT}[/bold] "
        f"→ {target.name} ({target.host}). Ctrl-C for å lukke."
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
