"""Deploying the Paperless stack to a target: files, systemd unit, start, health."""

from __future__ import annotations

import shlex
import time

from pless import composegen, config, sshexec, storage
from pless.targets import Host

DATA_SUBDIRS = ["consume", "export", "media", "data", "postgres", "redis", "backups"]

# How a registry says no. All of these read as a credentials fault and are
# usually an anonymous pull limit, which is the distinction worth making:
# one is something the operator must fix, the other is something they should
# simply try again.
REGISTRY_REFUSALS = (
    "unauthorized",
    "authentication required",
    "toomanyrequests",
    "too many requests",
    "denied: requested access",
    "rate limit",
)


class DeployError(RuntimeError):
    pass


def _run(
    target: Host, command: str, input_text: str | None = None, timeout: int = 120
) -> sshexec.SshResult:
    return sshexec.run(target.ssh_args, command, timeout=timeout, input_text=input_text)


def _run_ok(target: Host, command: str, input_text: str | None = None, timeout: int = 120) -> str:
    result = _run(target, command, input_text, timeout)
    if not result.ok:
        raise DeployError(
            f"Remote command failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _write_remote_file(
    target: Host, path: str, content: str, mode: str = "0644", owner: str | None = None
) -> None:
    """Write a file via stdin, so its contents never reach argv or a temp file."""
    quoted = shlex.quote(path)
    _run_ok(target, f"sudo tee {quoted} > /dev/null && sudo chmod {mode} {quoted}", content)
    if owner:
        _run_ok(target, f"sudo chown {owner} {quoted}")


def registry_refused(text: str) -> bool:
    """Does this failure read like a registry turning us away? Pure."""
    lowered = text.lower()
    return any(marker in lowered for marker in REGISTRY_REFUSALS)


def _rate_limit_note() -> str:
    return (
        "A registry refused to serve an image. That reads like a credentials problem and is "
        "usually an anonymous pull limit, so running the command again often works."
    )


def pull(target: Host, timeout: int = 1800) -> str:
    """Fetch the images ahead of the unit. Returns a problem, or "" if fine.

    `systemctl start` pulls as a side effect, and when a registry refuses
    mid-pull the unit fails and systemd's summary is all that comes back —
    which says nothing about registries. Doing it here first means the
    registry's own words are available to say so.

    Deliberately not fatal. `docker compose pull` contacts the registry even
    when every image is already local, so a rate-limited network would break a
    redeploy that would otherwise have started from cache. The job here is to
    explain a failure, not to cause one.
    """
    result = _run(
        target, f"cd {composegen.INSTALL_DIR} && sudo docker compose pull", timeout=timeout
    )
    if result.ok:
        return ""
    detail = result.stderr.strip() or result.stdout.strip()
    if registry_refused(detail):
        return f"{_rate_limit_note()}\n\n{detail}"
    return f"Fetching the images did not complete: {detail}"


def journal_content(text: str) -> str:
    """The journal's real lines, with its own markers stripped. Pure.

    `journalctl` answers `-- No entries --` and exits zero, and prints boot
    markers the same way. Treating that as content once cost the registry
    recognition below: the marker matched no failure shape, so the useful
    fallback was discarded and the operator lost the explanation. Silence
    arriving as a successful-looking string is exactly the trap this codebase
    keeps finding.
    """
    kept = [
        line
        for line in text.strip().splitlines()
        if line.strip() and not (line.strip().startswith("--") and line.strip().endswith("--"))
    ]
    return "\n".join(kept).strip()


def unit_journal(target: Host, unit: str, lines: int = 20) -> str:
    """The unit's own last words, for when systemd only offers its summary.

    Scoped to the attempt that just failed. `journalctl -n` returns the last
    lines whatever run produced them, so a unit that has failed before hands
    back a mixture of attempts — and a drill showed how easily an hour-old line
    reads as a fresh one. systemd's invocation id identifies this start
    exactly; where it is unavailable, the line count is the fallback.
    """
    quoted = shlex.quote(unit)
    invocation = _run(target, f"systemctl show -p InvocationID --value {quoted}")
    identifier = invocation.stdout.strip() if invocation.ok else ""
    if identifier:
        scoped = _run(
            target, f"sudo journalctl _SYSTEMD_INVOCATION_ID={shlex.quote(identifier)} --no-pager"
        )
        if scoped.ok and journal_content(scoped.stdout):
            return journal_content(scoped.stdout)
    result = _run(target, f"sudo journalctl -u {quoted} --no-pager -n {lines}")
    return journal_content(result.stdout) if result.ok else ""


def start_unit(target: Host, unit: str, timeout: int = 900, context: str = "") -> None:
    """Start a unit, and on failure say what the unit itself said.

    systemd reports that "the control process exited with error code" and
    points at `journalctl`. The cause is in the journal, and an operator who
    has to go and look has been handed a message that names nothing.
    """
    result = _run(target, f"sudo systemctl start {shlex.quote(unit)}", timeout=timeout)
    if result.ok:
        return

    detail = result.stderr.strip() or result.stdout.strip()
    if result.timed_out:
        raise DeployError(f"Starting {unit} timed out: {detail}")

    journal = unit_journal(target, unit)
    parts = [f"{unit} failed to start."]
    if registry_refused(journal) or (context and registry_refused(context)):
        parts.append(_rate_limit_note())
    elif context:
        # The pull already explained itself; the start failing makes that
        # explanation relevant rather than incidental.
        parts.append(context)
    if detail:
        parts.append(detail)
    if journal:
        parts.append(f"From the unit's own journal:\n{journal}")
    raise DeployError("\n\n".join(parts))


def install(cfg: config.Config, secrets: config.Secrets, target: Host) -> None:
    """Write the compose file, server-side .env and systemd unit, then start."""
    state = storage.status(cfg, target)
    if not state.is_mounted:
        raise DeployError(
            f"{storage.MOUNTPOINT} is not mounted — run `pless unlock` first. "
            "The stack must never be deployed outside the encrypted volume."
        )

    subdirs = " ".join(f"{composegen.INSTALL_DIR}/{d}" for d in DATA_SUBDIRS)
    _run_ok(target, f"sudo mkdir -p {subdirs}")
    # UID/GID 1000 matches USERMAP in the compose file; postgres owns its own directory.
    _run_ok(
        target,
        f"sudo chown -R 1000:1000 {composegen.INSTALL_DIR}/consume "
        f"{composegen.INSTALL_DIR}/export {composegen.INSTALL_DIR}/media "
        f"{composegen.INSTALL_DIR}/data",
    )

    _write_remote_file(
        target, f"{composegen.INSTALL_DIR}/docker-compose.yml", composegen.render_compose(cfg)
    )
    _write_remote_file(
        target,
        f"{composegen.INSTALL_DIR}/.env",
        composegen.render_server_env(secrets),
        mode="0600",
        owner="root:root",
    )
    _write_remote_file(target, "/etc/systemd/system/paperless.service", composegen.SYSTEMD_UNIT)
    _run_ok(target, "sudo systemctl daemon-reload")
    # Roughly 2 GB of images. Pulled here rather than as a side effect of the
    # unit, so that a registry refusing mid-pull is reported as such instead of
    # as "the control process exited with error code".
    problem = pull(target)
    start_unit(target, "paperless.service", context=problem)


def compose(target: Host, args: str, timeout: int = 120) -> str:
    return _run_ok(
        target, f"cd {composegen.INSTALL_DIR} && sudo docker compose {args}", timeout=timeout
    )


def http_status(target: Host) -> str:
    """HTTP status from Paperless on the target's localhost. '000' means no answer."""
    return _run_ok(
        target,
        f"curl -s -o /dev/null -w '%{{http_code}}' -m 5 http://127.0.0.1:{composegen.WEB_PORT} "
        "|| echo 000",
    )


def wait_healthy(target: Host, timeout_seconds: int = 300) -> bool:
    """Wait for the web server to answer. The first start migrates the database."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if http_status(target) in ("200", "302"):
            return True
        time.sleep(10)
    return False
