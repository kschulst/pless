"""Exposure audit: what can someone already on your network actually reach?

Threat model: an attacker already has access to the local network — a guest's
laptop, an unpatched IoT device, a compromised phone. They should find no
service to attack and no way to extract documents.

Collection happens in a single SSH round trip; the analysis is pure functions
so it can be tested without a machine, and reused by a web layer later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from pless import b2, backup, composegen, config, storage

# Loopback is fine to listen on; anything else is LAN exposure.
_LOOPBACK_PREFIXES = ("127.", "[::1]", "::1")

# What a key on the target must never hold. The first defeats Object Lock
# outright; the rest let a compromised machine reconfigure the bucket so that
# it can. A key created in B2's web console carries all of them.
FORBIDDEN_ON_THE_TARGET = (
    "bypassGovernance",
    "deleteBuckets",
    "deleteKeys",
    "writeBucketRetentions",
    "writeBuckets",
    "writeKeys",
)


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str
    severity: Severity = Severity.CRITICAL


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    @property
    def ok(self) -> bool:
        return not self.failures


# One round trip over SSH. Section markers make the output parseable without
# assuming an order, or that every command exists.
_EXPOSURE_COLLECT = f"""\
echo "##LISTEN"
sudo ss -tlnH 2>/dev/null || true
echo "##UFW"
sudo ufw status verbose 2>/dev/null || true
echo "##DOCKER"
sudo docker ps --format '{{{{.Names}}}} {{{{.Ports}}}}' 2>/dev/null || true
echo "##SSHD"
sudo sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin)' || true
echo "##MOUNT"
findmnt -no SOURCE {composegen.INSTALL_DIR} 2>/dev/null || true
"""

# --- Backup, and whether the bucket behind it actually protects anything ----
#
# The B2 calls run here rather than from the operator's machine because
# ADR 0020 gives the machine key `readBucketRetentions` precisely so that they
# can: an audit someone has to remember to run somewhere else is one they will
# not run. Credentials reach curl through the environment file, never argv.
#
# The condition is the presence of credentials, not the repository scheme.
# ADR 0017 requires the S3 endpoint, so the scheme is `s3:` while the
# credentials are still a B2 key.
# A raw string with one substitution, and raw for a reason. Two earlier
# versions were ordinary strings, and both lost backslashes to Python before
# the shell ever saw them: a sed expression's `\(` became an invalid escape,
# and `\"` inside the JSON body collapsed to a bare quote, so the shell
# stripped it and sent `{accountId:VALUE}` — unquoted and invalid. `sh -n`
# accepted every one of those, because they were valid shell saying the wrong
# thing.
#
# There is no `tr` either. `grep -o` finds every match whether the JSON arrives
# on one line or many, so splitting it first was never needed.
_BACKUP_COLLECT = r"""echo "##BACKUP"
if command -v restic > /dev/null 2>&1; then echo "restic: present"; else echo "restic: missing"; fi
echo "backup-timer: $(systemctl is-enabled pless-backup.timer 2>/dev/null || echo unknown)"
echo "verify-timer: $(systemctl is-enabled pless-backup-verify.timer 2>/dev/null || echo unknown)"
echo "##B2AUTH"
echo "##B2BUCKET"
if [ -r %(env_file)s ]; then
  . %(env_file)s
  B2_ID="${AWS_ACCESS_KEY_ID:-${B2_ACCOUNT_ID:-}}"
  B2_KEY="${AWS_SECRET_ACCESS_KEY:-${B2_ACCOUNT_KEY:-}}"
  if [ -n "$B2_ID" ] && [ -n "$B2_KEY" ]; then
    AUTHORIZE=%(api)s/b2api/v3/b2_authorize_account
    AUTH=$(curl -sS --max-time 15 -u "$B2_ID:$B2_KEY" "$AUTHORIZE" 2>/dev/null || true)
    FIELD='[[:space:]]*:[[:space:]]*"[^"]*"'
    TOKEN=$(printf '%%s' "$AUTH" | grep -o "\"authorizationToken\"$FIELD" | head -1 | cut -d'"' -f4)
    API=$(printf '%%s' "$AUTH" | grep -o "\"apiUrl\"$FIELD" | head -1 | cut -d'"' -f4)
    ACCOUNT=$(printf '%%s' "$AUTH" | grep -o "\"accountId\"$FIELD" | head -1 | cut -d'"' -f4)
    BUCKET=$(printf '%%s' "$AUTH" | grep -o "\"bucketId\"$FIELD" | head -1 | cut -d'"' -f4)
    echo "##B2AUTH"
    printf '%%s\n' "$AUTH"
    echo "##B2BUCKET"
    if [ -n "$TOKEN" ] && [ -n "$API" ] && [ -n "$BUCKET" ]; then
      BODY="{\"accountId\":\"$ACCOUNT\",\"bucketId\":\"$BUCKET\"}"
      curl -sS --max-time 15 -X POST "$API/b2api/v3/b2_list_buckets" -H "Authorization: $TOKEN" \
        -H 'Content-Type: application/json' -d "$BODY" 2>/dev/null || true
    fi
  fi
fi
"""

BACKUP_COLLECT_SCRIPT = _BACKUP_COLLECT % {
    "env_file": backup.ENV_FILE,
    "api": "https://api.backblazeb2.com",
}

# Still one round trip. The backup section guards on the environment file, so a
# machine with no backup configured runs two `echo`s and nothing else.
COLLECT_SCRIPT = _EXPOSURE_COLLECT + BACKUP_COLLECT_SCRIPT


def split_sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("##"):
            current = line[2:].strip().lower()
            sections[current] = []
        elif current:
            stripped = line.strip()
            if stripped:
                sections[current].append(stripped)
    return sections


def _is_loopback(address: str) -> bool:
    return any(address.startswith(prefix) for prefix in _LOOPBACK_PREFIXES)


def parse_listening_addresses(ss_lines: list[str]) -> list[str]:
    """Extract 'address:port' from `ss -tlnH` lines (column 4)."""
    addresses = []
    for line in ss_lines:
        fields = line.split()
        if len(fields) >= 4:
            addresses.append(fields[3])
    return addresses


# Make findings readable: a bare port number says little about what to fix.
KNOWN_PORTS = {
    "22": "SSH",
    "5353": "mDNS",
    "5355": "LLMNR",
    "8000": "Paperless",
    "5432": "Postgres",
    "6379": "Redis",
}


def describe_address(address: str) -> str:
    port = address.rsplit(":", 1)[-1]
    name = KNOWN_PORTS.get(port)
    return f"{address} ({name})" if name else address


def check_listening_sockets(ss_lines: list[str]) -> Finding:
    exposed = [addr for addr in parse_listening_addresses(ss_lines) if not _is_loopback(addr)]
    if not exposed:
        return Finding(
            check="listening sockets",
            ok=True,
            detail="Nothing listens outside loopback — your LAN sees zero ports.",
        )
    described = ", ".join(describe_address(addr) for addr in exposed)
    return Finding(
        check="listening sockets",
        ok=False,
        detail=(f"Exposed to LAN: {described}. Run `pless harden` to bind SSH to tailscale0."),
    )


def check_ufw(ufw_lines: list[str]) -> Finding:
    text = " ".join(ufw_lines).lower()
    if "status: active" not in text:
        return Finding(check="firewall", ok=False, detail="UFW is not active.")
    if "deny (incoming)" not in text:
        return Finding(
            check="firewall", ok=False, detail="UFW does not default-deny incoming traffic."
        )
    # "Anywhere" without an interface binding means open to the LAN.
    open_to_lan = [
        line
        for line in ufw_lines
        if "allow in" in line.lower()
        and "anywhere" in line.lower()
        and "tailscale" not in line.lower()
        and "(v6)" not in line.lower()
    ]
    if open_to_lan:
        return Finding(
            check="firewall",
            ok=False,
            detail=f"Rules open to the whole LAN: {'; '.join(open_to_lan)}",
        )
    return Finding(check="firewall", ok=True, detail="UFW active, default deny, no LAN-open rules.")


def check_docker_ports(docker_lines: list[str]) -> Finding:
    """Docker writes its own iptables rules, bypassing UFW — a port published
    without a 127.0.0.1 prefix is LAN-visible even though UFW says deny."""
    leaked = []
    for line in docker_lines:
        name, _, ports = line.partition(" ")
        for mapping in ports.split(","):
            mapping = mapping.strip()
            if "->" not in mapping:
                continue  # container-internal port only, not published
            if not mapping.startswith("127.0.0.1:"):
                leaked.append(f"{name}: {mapping}")
    if leaked:
        return Finding(
            check="docker ports",
            ok=False,
            detail=(
                f"Published outside loopback (bypasses UFW!): {'; '.join(leaked)}. "
                "All ports must be bound with a 127.0.0.1 prefix."
            ),
        )
    return Finding(check="docker ports", ok=True, detail="No container publishes outside loopback.")


def check_sshd(sshd_lines: list[str]) -> Finding:
    settings = dict(line.split(None, 1) for line in sshd_lines if " " in line)
    if settings.get("passwordauthentication", "").strip() != "no":
        return Finding(check="ssh", ok=False, detail="Password authentication is not disabled.")
    return Finding(check="ssh", ok=True, detail="Key-based authentication only.")


def check_encrypted_storage(mount_lines: list[str]) -> Finding:
    source = mount_lines[0] if mount_lines else ""
    if not source:
        return Finding(
            check="encrypted storage",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} is not mounted — is the volume locked?",
            severity=Severity.WARNING,
        )
    if storage.MAPPER_NAME not in source:
        return Finding(
            check="encrypted storage",
            ok=False,
            detail=f"{composegen.INSTALL_DIR} is on {source}, not on the LUKS device.",
        )
    return Finding(
        check="encrypted storage", ok=True, detail=f"Data lives on the LUKS device {source}."
    )


# --- Backup checks ---------------------------------------------------------
#
# These reuse `b2`'s parsers and its five-way lock classification rather than
# repeating them. Two implementations of that classification is how they drift,
# and a drifted copy is exactly the "looks protected and is not" failure the
# check exists to catch.


def check_backup_installed(backup_lines: list[str]) -> Finding:
    """Is anything backing up at all?"""
    text = " ".join(backup_lines)
    if "restic: missing" in text:
        return Finding(
            check="backup installed",
            ok=False,
            detail=(
                "restic is not installed, so nothing on this machine can take a backup. "
                "Run `pless bootstrap`."
            ),
        )
    timers = [line for line in backup_lines if line.startswith(("backup-timer:", "verify-timer:"))]
    inactive = [line for line in timers if "enabled" not in line]
    if inactive:
        return Finding(
            check="backup installed",
            ok=False,
            detail=(
                "restic is present but a timer is not enabled: "
                + "; ".join(inactive)
                + ". Run `pless backup init`."
            ),
            severity=Severity.WARNING,
        )
    return Finding(
        check="backup installed",
        ok=True,
        detail="restic is installed and both timers are enabled.",
    )


def check_backup_credential(auth: b2.Authorization) -> Finding:
    """Can the key on this machine do more than take backups?

    The threat model is a stolen or compromised machine (ADR 0005), so what
    matters is the ceiling on what its credential can do — not what restic
    happens to use.
    """
    dangerous = [c for c in FORBIDDEN_ON_THE_TARGET if c in auth.capabilities]
    if dangerous:
        return Finding(
            check="backup credential",
            ok=False,
            detail=(
                f"The key on this machine holds {', '.join(dangerous)}. "
                + (
                    "bypassGovernance defeats Object Lock outright, which is the one thing "
                    "protecting your history from a compromised machine. "
                    if "bypassGovernance" in dangerous
                    else "Those let a compromised machine reconfigure the bucket itself. "
                )
                + "A key created in B2's web console carries all of them. Mint a correct one "
                "with `pless b2 provision --new-key`."
            ),
        )

    if not auth.is_bucket_restricted:
        return Finding(
            check="backup credential",
            ok=False,
            detail=(
                "The key on this machine is not restricted to a single bucket, so it reaches "
                "every bucket in the account. `pless b2 provision` mints one that is."
            ),
        )

    if "readBucketRetentions" not in auth.capabilities:
        return Finding(
            check="backup credential",
            ok=False,
            detail=(
                "The key was minted without readBucketRetentions, so this machine cannot read "
                "the Object Lock configuration it depends on — and the bucket check below "
                "cannot run. A protection you cannot verify is one you are trusting rather "
                "than checking (ADR 0020). `pless b2 provision --new-key` mints a correct key."
            ),
        )

    if auth.key_expires_at:
        return Finding(
            check="backup credential",
            ok=False,
            detail=(
                f"The key is correctly scoped to {auth.bucket_name or 'one bucket'}, but it "
                f"expires at {auth.key_expires_at}. When it does, backups begin failing on a "
                "day nobody chose — quietly, because a failing timer is quiet. Mint one "
                "without an expiry with `pless b2 provision --new-key`."
            ),
            severity=Severity.WARNING,
        )

    return Finding(
        check="backup credential",
        ok=True,
        detail=(
            f"Scoped to {auth.bucket_name or 'one bucket'}, and cannot change bucket settings "
            "or bypass governance."
        ),
    )


def check_bucket_protection(lock: b2.LockConfiguration, required_days: int) -> Finding:
    """Does the bucket actually hold versions against a compromised machine?

    Five outcomes, because B2 reports five states and any two of them conflated
    produces a check that is either useless or dangerous.
    """
    state = lock.state()

    if state is b2.LockState.UNREADABLE:
        return Finding(
            check="bucket protection",
            ok=False,
            detail=(
                "Backblaze withheld the bucket's Object Lock configuration from this key, so "
                "nothing here is verified — this is not the same as the bucket being "
                "unprotected, and pless will not guess which it is. See the credential "
                "finding above."
            ),
        )

    if state is b2.LockState.DISABLED:
        return Finding(
            check="bucket protection",
            ok=False,
            detail=(
                "The bucket has no Object Lock, so this machine's key can permanently delete "
                "every version in it. Object Lock cannot be added to an existing bucket, so "
                "this needs a new bucket: `pless b2 provision --bucket <name>`."
            ),
        )

    if state is b2.LockState.ENABLED_WITHOUT_RETENTION:
        return Finding(
            check="bucket protection",
            ok=False,
            detail=(
                "Object Lock is enabled on the bucket but no default retention is set, so "
                "nothing written to it is retained at all. This looks like protection and is "
                "none — it is what a bucket looks like when Object Lock was switched on and "
                "nothing else was. Re-run `pless b2 provision` to set the retention."
            ),
        )

    if state is b2.LockState.COMPLIANCE:
        return Finding(
            check="bucket protection",
            ok=False,
            detail=(
                "The bucket is in compliance mode. It does protect your history, and it binds "
                "you as much as an attacker: nothing can be deleted before expiry by anyone, "
                "and Backblaze's remedy for a period set too long is closing the account. "
                "Governance is what this design wants."
            ),
            severity=Severity.WARNING,
        )

    try:
        days = lock.period.as_days() if lock.period else 0
    except b2.B2Error as exc:
        return Finding(check="bucket protection", ok=False, detail=str(exc))

    if days < required_days:
        return Finding(
            check="bucket protection",
            ok=False,
            detail=(
                f"The bucket retains versions for {days} days, but [backup] "
                f"version_retention_days expects {required_days}. Re-run "
                "`pless b2 provision` to raise it."
            ),
        )

    return Finding(
        check="bucket protection",
        ok=True,
        detail=(
            f"Object Lock in governance mode, retaining versions for {days} days — so this "
            "machine's key can delete files and cannot destroy history."
        ),
    )


def check_repository_endpoint(repository: str, s3_api_url: str) -> Finding:
    """Does the configured repository name the account that just authenticated?

    The machine key can read the account's own endpoint, so this is checkable
    rather than assumed — and a repository pointing somewhere that merely
    resembles the account is a backup going somewhere nobody is watching.
    """
    if not s3_api_url or not repository.startswith("s3:"):
        return Finding(
            check="repository endpoint",
            ok=True,
            detail="Not an S3 repository, so there is no endpoint to compare.",
        )
    if s3_api_url.rstrip("/") not in repository:
        return Finding(
            check="repository endpoint",
            ok=False,
            detail=(
                "The configured repository does not sit under the endpoint this account "
                "reports, so backups may be going to a different account than the one whose "
                "bucket was just checked."
            ),
            severity=Severity.WARNING,
        )
    return Finding(
        check="repository endpoint",
        ok=True,
        detail="The repository sits under this account's own S3 endpoint.",
    )


def analyse(output: str, cfg: config.Config | None = None) -> AuditReport:
    """The exposure findings, plus the backup findings when configuration is known.

    `cfg` stays optional so every existing caller and test is unaffected: the
    backup checks need `version_retention_days` and the repository location,
    and without them there is nothing to compare against.
    """
    sections = split_sections(output)
    findings = [
        check_listening_sockets(sections.get("listen", [])),
        check_ufw(sections.get("ufw", [])),
        check_docker_ports(sections.get("docker", [])),
        check_sshd(sections.get("sshd", [])),
        check_encrypted_storage(sections.get("mount", [])),
    ]

    if cfg is not None and cfg.backup.is_configured:
        findings.append(check_backup_installed(sections.get("backup", [])))

        auth_json = "\n".join(sections.get("b2auth", []))
        if auth_json.strip():
            try:
                auth = b2.parse_authorization(json.loads(auth_json))
            except (ValueError, b2.B2Error):
                findings.append(
                    Finding(
                        check="backup credential",
                        ok=False,
                        detail=(
                            "Backblaze did not answer with a usable authorization, so the key "
                            "on this machine could not be checked. An unverifiable credential "
                            "is not a passing one."
                        ),
                    )
                )
            else:
                findings.append(check_backup_credential(auth))
                findings.append(
                    check_repository_endpoint(cfg.backup.restic_repository, auth.s3_api_url)
                )

                bucket_json = "\n".join(sections.get("b2bucket", []))
                lock = b2.LockConfiguration()
                if bucket_json.strip():
                    try:
                        payload = json.loads(bucket_json)
                    except ValueError:
                        payload = {}
                    for bucket in payload.get("buckets") or []:
                        lock = b2.parse_lock_configuration(bucket)
                findings.append(check_bucket_protection(lock, cfg.backup.version_retention_days))

    return AuditReport(findings=findings)
