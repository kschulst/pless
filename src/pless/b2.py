"""Obtaining a Backblaze B2 bucket that Object Lock actually protects.

Everything here exists because Backblaze's web console cannot produce the
arrangement [ADR 0017](../../adr/0017-tamper-resistance-in-the-bucket.md)
depends on. A console key marked "Read and Write" arrives holding all 29
capabilities — including `bypassGovernance`, the one that defeats Object Lock —
and ignores the bucket restriction that was selected. Its create-bucket dialog
offers Object Lock only in compliance mode. Both were confirmed against a real
account.

The load-bearing property is a refusal, not a creation. B2 will not issue
capabilities a parent key lacks, so a provisioning credential without
`bypassGovernance` *cannot* mint a machine key with it — not by accident, and
not if this code is wrong. `provision` therefore inspects the credential and
stops before touching anything, which is what makes a dangerous machine key
unobtainable rather than merely unlikely (ADR 0021).

This is the first core module that resolves no host: it imports neither
`targets` nor `sshexec`, and an import of either here is a mistake. Everything
that decides something is a pure function over parsed responses, so the
security-relevant judgements are testable without an account; the network lives
behind a single injected callable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

API_URL = "https://api.backblazeb2.com"
API_VERSION = "v3"

# The six from ADR 0017 and ADR 0020. Five let restic work — it creates and
# removes lock files, so it cannot run without delete — and the sixth lets the
# machine read the lock it depends on, because a protection you cannot verify
# is one you are trusting rather than checking.
MACHINE_KEY_CAPABILITIES = (
    "deleteFiles",
    "listBuckets",
    "listFiles",
    "readBucketRetentions",
    "readFiles",
    "writeFiles",
)

# The single exclusion that makes a dangerous machine key unobtainable. A key
# cannot grant what it does not hold, so withholding this from the provisioning
# credential withholds it from everything the credential can create.
FORBIDDEN_IN_PROVISIONING_KEY = ("bypassGovernance",)

# Creating a bucket, setting its retention, and minting a key — plus listing
# keys, which B2 treats as a capability of its own. A credential with
# `writeKeys` alone can mint a key and not see one, and a verification run
# found that out with an HTTP 401 after the bucket already existed.
REQUIRED_IN_PROVISIONING_KEY = (
    "listKeys",
    "writeBucketRetentions",
    "writeBuckets",
    "writeKeys",
)

MACHINE_KEY_NAME = "pless-machine"
PROVISIONING_KEY_NAME = "pless-provisioning"

# B2's documented rule: 6-50 characters, lowercase letters, digits and hyphens,
# not starting or ending with a hyphen. `b2-` is reserved.
BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{4,48}[a-z0-9]$")
RESERVED_BUCKET_PREFIX = "b2-"

# What B2 says when a name is taken. Because `provision` lists before it
# creates, by the time this arrives it can only mean another account holds it.
DUPLICATE_BUCKET_CODE = "duplicate_bucket_name"

# Retention periods come back as an object with a unit, so the unit is read
# before the duration is used. A bucket configured in some other unit must fail
# loudly rather than be assumed to be days.
DAYS_PER_UNIT = {"days": 1, "years": 365}


class B2Error(RuntimeError):
    """A refusal, optionally carrying the script that remedies it.

    The remedy is data rather than 36 lines of message text. A verification run
    made the case: a refusal that printed the bootstrap script inline came to 51
    lines, and its three load-bearing sentences scrolled away above them.

    `cli.py` writes the script somewhere and prints one line to run; a web
    interface would render the same field as a download. Neither is this
    module's business (ADR 0009), and printing it from here would have made the
    second impossible.
    """

    def __init__(self, message: str, script: str = "") -> None:
        super().__init__(message)
        self.script = script


class LockState(StrEnum):
    """The five states B2 distinguishes, and conflating any two is harmful.

    `UNREADABLE` is not the absence of a lock: B2 returns the field with its
    value withheld when the key may not read it. `ENABLED_WITHOUT_RETENTION` is
    the dangerous one — it is what every bucket looks like between creation and
    the call that sets retention, and it looks like success to anything that
    asks only whether the lock is on.
    """

    UNREADABLE = "unreadable"
    DISABLED = "disabled"
    ENABLED_WITHOUT_RETENTION = "enabled-without-retention"
    COMPLIANCE = "compliance"
    GOVERNANCE = "governance"


@dataclass
class ProvisioningCredential:
    """An operator-side B2 key, held in memory and written nowhere.

    `__repr__` is overridden because the leak path for this is output rather
    than argv: a traceback, a log line, or an error pasted into an issue. ADR
    0014 moved secrets out of argv for the same reason this keeps them out of
    anything rendered.
    """

    key_id: str
    application_key: str

    def __repr__(self) -> str:
        return f"ProvisioningCredential(key_id={self.key_id!r}, application_key=<redacted>)"


@dataclass
class Authorization:
    """What B2 says about the credential that just authenticated."""

    account_id: str = ""
    api_url: str = ""
    s3_api_url: str = ""
    capabilities: list[str] = field(default_factory=list)
    bucket_id: str = ""
    bucket_name: str = ""
    key_expires_at: str = ""
    # Carries the full capabilities of whatever authenticated, for 24 hours.
    # Never rendered, hence repr=False.
    token: str = field(default="", repr=False)

    @property
    def is_bucket_restricted(self) -> bool:
        return bool(self.bucket_id)


@dataclass
class RetentionPeriod:
    duration: int
    unit: str

    def as_days(self) -> int:
        """Days, or a refusal — never a guess.

        Assuming days would silently pass a bucket configured in another unit,
        which is exactly the class of mistake this whole check exists to catch.
        """
        if self.unit not in DAYS_PER_UNIT:
            raise B2Error(
                f"The bucket's retention period is expressed in {self.unit!r}, which pless "
                f"does not recognise. Known units: {', '.join(sorted(DAYS_PER_UNIT))}. "
                "Refusing to guess, because guessing wrong here would report a bucket as "
                "protected when it is not."
            )
        return self.duration * DAYS_PER_UNIT[self.unit]


@dataclass
class LockConfiguration:
    authorized_to_read: bool = False
    enabled: bool = False
    mode: str = ""
    period: RetentionPeriod | None = None

    def state(self) -> LockState:
        if not self.authorized_to_read:
            return LockState.UNREADABLE
        if not self.enabled:
            return LockState.DISABLED
        if self.mode == "compliance":
            return LockState.COMPLIANCE
        if self.mode == "governance" and self.period is not None:
            return LockState.GOVERNANCE
        return LockState.ENABLED_WITHOUT_RETENTION


@dataclass
class Bucket:
    bucket_id: str
    bucket_name: str
    lock: LockConfiguration = field(default_factory=LockConfiguration)


@dataclass
class MachineKey:
    """The key the target will hold. Disclosed once and never stored by pless."""

    key_id: str
    application_key: str
    capabilities: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"MachineKey(key_id={self.key_id!r}, application_key=<redacted>)"


@dataclass
class ProvisionOutcome:
    bucket: Bucket
    machine_key: MachineKey
    repository: str
    notes: list[str] = field(default_factory=list)
    created_bucket: bool = False
    repaired_retention: bool = False


class Transport(Protocol):
    """The seam. The only thing that touches the network, and the only fake.

    A non-2xx status is *returned* rather than raised, so the orchestration can
    interpret `duplicate_bucket_name` instead of being thrown by it.
    """

    def __call__(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        credential: ProvisioningCredential | str | None = None,
    ) -> tuple[int, dict]: ...


# --- Pure decisions -------------------------------------------------------
#
# Everything below this line is a function over parsed data with no I/O, so the
# judgements that matter can be tested without an account.


def validate_bucket_name(name: str) -> None:
    """Check the name against B2's rule, naming the rule that was broken.

    Locally, before any call, so a bad name costs nothing — and so the message
    says which rule failed rather than relaying a generic rejection.
    """
    if not name:
        raise B2Error("A bucket name is required. Pass --bucket.")
    if name.startswith(RESERVED_BUCKET_PREFIX):
        raise B2Error(
            f"Bucket names cannot start with {RESERVED_BUCKET_PREFIX!r} — Backblaze reserves "
            "that prefix."
        )
    if not 6 <= len(name) <= 50:
        raise B2Error(f"A bucket name is 6 to 50 characters; {name!r} is {len(name)}.")
    if name.startswith("-") or name.endswith("-"):
        raise B2Error(f"A bucket name cannot start or end with a hyphen; {name!r} does.")
    if not BUCKET_NAME_PATTERN.match(name):
        raise B2Error(
            f"{name!r} is not a valid bucket name. Lowercase letters, digits and hyphens only."
        )


def parse_authorization(payload: dict) -> Authorization:
    """Read `b2_authorize_account`'s answer, tolerating both API shapes.

    v3 nests the account information under `apiInfo.storageApi`; v2 had it at
    the top level under `allowed`. A field that is missing is treated as
    missing, never as a default.
    """
    token = str(payload.get("authorizationToken") or "")
    if not token:
        raise B2Error(
            "Backblaze did not return an authorization token, so the credential could not "
            "be inspected. Refusing to continue: an unverifiable credential is never "
            "assumed to be acceptable."
        )
    storage = payload.get("apiInfo", {}).get("storageApi") or payload.get("allowed") or {}
    return Authorization(
        account_id=str(payload.get("accountId") or ""),
        api_url=str(storage.get("apiUrl") or payload.get("apiUrl") or ""),
        s3_api_url=str(storage.get("s3ApiUrl") or payload.get("s3ApiUrl") or ""),
        capabilities=[str(c) for c in (storage.get("capabilities") or [])],
        bucket_id=str(storage.get("bucketId") or ""),
        bucket_name=str(storage.get("bucketName") or ""),
        key_expires_at=str(payload.get("applicationKeyExpirationTimestamp") or ""),
        token=token,
    )


def parse_lock_configuration(payload: dict) -> LockConfiguration:
    """Read a bucket's `fileLockConfiguration`.

    `isClientAuthorizedToRead: false` means the value was *withheld*, which is
    not the same as a bucket without a lock — and a check that conflated the
    two would either report a correct bucket as broken every time, or certify a
    destroyable one by silence.
    """
    lock = payload.get("fileLockConfiguration") or {}
    if not lock.get("isClientAuthorizedToRead"):
        return LockConfiguration(authorized_to_read=False)

    value = lock.get("value") or {}
    retention = value.get("defaultRetention") or {}
    period_payload = retention.get("period") or {}
    period = None
    if period_payload.get("duration") is not None:
        period = RetentionPeriod(
            duration=int(period_payload["duration"]),
            unit=str(period_payload.get("unit") or ""),
        )
    return LockConfiguration(
        authorized_to_read=True,
        enabled=bool(value.get("isFileLockEnabled")),
        mode=str(retention.get("mode") or ""),
        period=period,
    )


def parse_bucket(payload: dict) -> Bucket:
    return Bucket(
        bucket_id=str(payload.get("bucketId") or ""),
        bucket_name=str(payload.get("bucketName") or ""),
        lock=parse_lock_configuration(payload),
    )


BOOTSTRAP_SCRIPT_PATH = "/tmp/pless-provisioning-key.py"

# The body of the bootstrap script, with three fields filled in. Python rather
# than shell: the first version prompted with `read -r -s -p`, which is bash —
# in zsh that flag means "read from a coprocess", so the prompts never appeared
# and the chain collapsed four commands later. A snippet whose only job is to
# work when pasted cannot depend on which shell pastes it.
_BOOTSTRAP_SCRIPT = """\
import base64
import getpass
import json
import urllib.request

key_id = getpass.getpass("B2 master keyID: ")
app_key = getpass.getpass("B2 master applicationKey: ")


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


basic = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
auth = call(
    "%(api_url)s/b2api/%(version)s/b2_authorize_account",
    headers={"Authorization": "Basic " + basic},
)
storage = auth["apiInfo"]["storageApi"]
created = call(
    storage["apiUrl"] + "/b2api/%(version)s/b2_create_key",
    {
        "accountId": "%(account_id)s",
        "keyName": "%(key_name)s",
        "capabilities": %(capabilities)s,
    },
    {"Authorization": auth["authorizationToken"], "Content-Type": "application/json"},
)
print()
print("keyID         :", created["applicationKeyId"])
print("applicationKey:", created["applicationKey"])
print()
print("Shown once. Save both, then run `pless b2 provision`.")
"""


def bootstrap_key_command(account_id: str) -> str:
    """The call that mints a correct provisioning key, ready to paste.

    Printed by the refusal rather than kept in prose, and with nothing to
    substitute: a grilling session watched the hand-run path fail twice, first
    on placeholders pasted literally and then on `$EDITOR` being unset.
    Instructions requiring substitution are where operators go wrong.

    The heredoc feeds `cat`, not `python3`. Piping it into `python3 -` would
    make stdin the script itself, and `getpass` would read the script's own
    remaining lines instead of prompting.

    The master key is prompted, used once, and never written. It deliberately
    never reaches pless — there is no code path here that accepts it (ADR 0021).
    """
    script = bootstrap_key_script(account_id)
    return (
        f"cat > {BOOTSTRAP_SCRIPT_PATH} <<'PLESS_EOF'\n"
        f"{script}"
        "PLESS_EOF\n"
        f"python3 {BOOTSTRAP_SCRIPT_PATH}; rm -f {BOOTSTRAP_SCRIPT_PATH}"
    )


def bootstrap_key_script(account_id: str) -> str:
    """Just the script, for a caller that would rather save it than print it.

    Contains no secret: it prompts for the master key, uses it, and writes
    nothing. So it is safe to put on disk where the operator can run it.
    """
    capabilities = sorted({*MACHINE_KEY_CAPABILITIES, *REQUIRED_IN_PROVISIONING_KEY})
    return _BOOTSTRAP_SCRIPT % {
        "api_url": API_URL,
        "version": API_VERSION,
        "account_id": account_id,
        "key_name": PROVISIONING_KEY_NAME,
        "capabilities": json.dumps(capabilities),
    }


def refuse_provisioning_credential(auth: Authorization) -> None:
    """Stop before anything is created, unless the credential is acceptable.

    This is the feature. Without it `provision` would merely *promise* to omit
    `bypassGovernance`, and a promise is what ADR 0017 ruled insufficient.
    """
    remedy = bootstrap_key_script(auth.account_id)

    held = [c for c in FORBIDDEN_IN_PROVISIONING_KEY if c in auth.capabilities]
    if held:
        raise B2Error(
            f"This credential holds {', '.join(held)}, so every key it creates could hold it "
            "too — B2 cannot be asked to withhold what the parent key has. That capability "
            "is the one thing Object Lock protects against, so pless will not use this "
            "credential. Mint one without it and run this again.",
            script=remedy,
        )

    if auth.is_bucket_restricted:
        raise B2Error(
            f"This credential is restricted to the bucket {auth.bucket_name or auth.bucket_id!r}, "
            "so it cannot create another one. Provisioning needs an account-wide credential "
            "that still lacks bypassGovernance.",
            script=remedy,
        )

    missing = [c for c in REQUIRED_IN_PROVISIONING_KEY if c not in auth.capabilities]
    if missing:
        raise B2Error(
            f"This credential is missing {', '.join(missing)}, so it cannot create a bucket, "
            "set its retention and mint a key.",
            script=remedy,
        )


def repository_for(s3_api_url: str, bucket_name: str) -> str:
    """The `restic_repository` value, in the form ADR 0017 specifies.

    Addressed through B2's S3 endpoint rather than the native `b2:` backend,
    because a delete through the S3 API becomes a delete marker, which Object
    Lock permits — and the native backend removes file versions, which it does
    not.
    """
    if not s3_api_url:
        raise B2Error(
            "Backblaze did not report an S3 endpoint for this account, so the repository "
            "location cannot be assembled."
        )
    return f"s3:{s3_api_url.rstrip('/')}/{bucket_name}"


def retention_satisfies(lock: LockConfiguration, required_days: int) -> bool:
    """Does this bucket actually protect versions for long enough?"""
    if lock.state() is not LockState.GOVERNANCE or lock.period is None:
        return False
    return lock.period.as_days() >= required_days


def render_api_error(status: int, payload: dict | None, body: str = "") -> str:
    """Turn a refusal into text, without ever rendering what authorised it.

    The realistic leak path is not argv but a rendered error pasted into an
    issue, and this project settles arguments by pasting real API output. So
    the message is reconstructed from the status and B2's own error body; the
    `Authorization` header and the bearer token appear nowhere.
    """
    if payload:
        code = str(payload.get("code") or "")
        message = str(payload.get("message") or "")
        if code and not message:
            # B2 answers some refusals with a code and nothing else, and a
            # message that trails off after a colon tells the operator less
            # than the code alone would.
            return f"Backblaze refused the request (HTTP {status}) with the code {code!r}."
        if code or message:
            return f"Backblaze refused the request (HTTP {status}, {code or 'no code'}): {message}"
    if body.strip():
        return f"Backblaze refused the request (HTTP {status}): {body.strip()[:300]}"
    return f"Backblaze refused the request (HTTP {status}), with no explanation in the body."


# --- Transport ------------------------------------------------------------


def http_transport(timeout: int = 30) -> Transport:
    """The only function here that touches the network.

    No retries: every call either changes state or decides whether to, so a
    silent second attempt could create a second bucket or a second key.
    """

    def transport(
        method: str,
        url: str,
        body: dict | None = None,
        credential: ProvisioningCredential | str | None = None,
    ) -> tuple[int, dict]:
        import httpx

        headers = {"Content-Type": "application/json"}
        auth = None
        if isinstance(credential, ProvisioningCredential):
            auth = (credential.key_id, credential.application_key)
        elif credential:
            headers["Authorization"] = credential

        try:
            response = httpx.request(
                method, url, json=body, headers=headers, auth=auth, timeout=timeout
            )
        except httpx.HTTPError as exc:
            # str(exc) carries the URL but never a header, so this is safe to
            # render. Anything that would carry one is not reported.
            raise B2Error(f"Could not reach Backblaze: {exc}") from None

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return response.status_code, payload

    return transport


def _call(
    transport: Transport,
    auth: Authorization,
    endpoint: str,
    body: dict | None = None,
) -> dict:
    """One API call that must succeed, so the orchestration reads as a sequence."""
    url = f"{auth.api_url.rstrip('/')}/b2api/{API_VERSION}/{endpoint}"
    status, payload = transport("POST", url, body, auth.token)
    if status in (401, 403):
        # The credential authorised fine, so a refusal on a single call is
        # almost always a capability it does not hold for that operation —
        # which is otherwise a very quiet thing to debug.
        raise B2Error(
            f"{render_api_error(status, payload)} The credential authorised, so this is most "
            f"likely a capability it does not hold for {endpoint}."
        )
    if not 200 <= status < 300:
        raise B2Error(render_api_error(status, payload))
    return payload


# --- Orchestration --------------------------------------------------------


def authorize(credential: ProvisioningCredential, transport: Transport) -> Authorization:
    status, payload = transport(
        "GET", f"{API_URL}/b2api/{API_VERSION}/b2_authorize_account", None, credential
    )
    if not 200 <= status < 300:
        raise B2Error(
            f"{render_api_error(status, payload)} The credential could not be inspected, so "
            "pless will not proceed with it."
        )
    return parse_authorization(payload)


def find_bucket(auth: Authorization, name: str, transport: Transport) -> Bucket | None:
    """Look the name up in this account, before anything tries to create it.

    This ordering is what makes a later `duplicate_bucket_name` unambiguous:
    B2 bucket names are globally unique, so without it a plausible name yields
    an error that cannot say whether the operator owns the bucket already or a
    stranger holds it.
    """
    payload = _call(
        transport,
        auth,
        "b2_list_buckets",
        {"accountId": auth.account_id, "bucketName": name},
    )
    for bucket in payload.get("buckets") or []:
        if bucket.get("bucketName") == name:
            return parse_bucket(bucket)
    return None


def create_bucket(auth: Authorization, name: str, transport: Transport) -> Bucket:
    """Create it with Object Lock on, because it cannot be added afterwards."""
    url = f"{auth.api_url.rstrip('/')}/b2api/{API_VERSION}/b2_create_bucket"
    status, payload = transport(
        "POST",
        url,
        {
            "accountId": auth.account_id,
            "bucketName": name,
            "bucketType": "allPrivate",
            "fileLockEnabled": True,
        },
        auth.token,
    )
    if 200 <= status < 300:
        return parse_bucket(payload)

    if payload.get("code") == DUPLICATE_BUCKET_CODE:
        raise B2Error(
            f"The name {name!r} is taken by another Backblaze account. Bucket names are "
            "globally unique across all of B2, and this account does not hold one by that "
            "name — pless checked before trying to create it. Choose a different name."
        )
    raise B2Error(render_api_error(status, payload))


def set_governance_retention(
    auth: Authorization, bucket: Bucket, days: int, transport: Transport
) -> Bucket:
    """Governance, not compliance.

    The console offers only compliance, which binds the operator as much as an
    attacker — and Backblaze's remedy for a period set too long is closing the
    account.
    """
    payload = _call(
        transport,
        auth,
        "b2_update_bucket",
        {
            "accountId": auth.account_id,
            "bucketId": bucket.bucket_id,
            "defaultRetention": {
                "mode": "governance",
                "period": {"duration": days, "unit": "days"},
            },
        },
    )
    return parse_bucket(payload)


def count_objects(auth: Authorization, bucket: Bucket, transport: Transport) -> int:
    """Whether the bucket holds anything — not how much. Never enumerates."""
    payload = _call(
        transport,
        auth,
        "b2_list_file_versions",
        {"bucketId": bucket.bucket_id, "maxFileCount": 1},
    )
    return len(payload.get("files") or [])


def existing_machine_keys(auth: Authorization, bucket: Bucket, transport: Transport) -> list[str]:
    payload = _call(
        transport, auth, "b2_list_keys", {"accountId": auth.account_id, "maxKeyCount": 100}
    )
    return [
        str(key.get("applicationKeyId"))
        for key in payload.get("keys") or []
        if key.get("bucketId") == bucket.bucket_id
    ]


def mint_machine_key(auth: Authorization, bucket: Bucket, transport: Transport) -> MachineKey:
    """Mint the key the target will hold, and check what came back.

    No `validDurationInSeconds`: an expiring machine key makes backups fail on
    a day nobody chose, quietly, because a failing timer is quiet.
    """
    payload = _call(
        transport,
        auth,
        "b2_create_key",
        {
            "accountId": auth.account_id,
            "bucketId": bucket.bucket_id,
            "keyName": MACHINE_KEY_NAME,
            "capabilities": list(MACHINE_KEY_CAPABILITIES),
        },
    )
    granted = sorted(str(c) for c in payload.get("capabilities") or [])
    if granted != sorted(MACHINE_KEY_CAPABILITIES):
        raise B2Error(
            "Backblaze returned a key with capabilities pless did not ask for: "
            f"{', '.join(granted) or 'none'}. Expected exactly "
            f"{', '.join(sorted(MACHINE_KEY_CAPABILITIES))}. Not reporting success on a key "
            "whose rights are not the ones that were requested."
        )
    return MachineKey(
        key_id=str(payload.get("applicationKeyId") or ""),
        application_key=str(payload.get("applicationKey") or ""),
        capabilities=granted,
    )


def provision(
    credential: ProvisioningCredential,
    bucket_name: str,
    retention_days: int,
    transport: Transport,
    allow_new_key: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ProvisionOutcome:
    """Create or adopt a bucket Object Lock protects, and mint a key for it.

    Nothing is created before the credential has been inspected and accepted,
    and success is claimed only after the final state has been read back: the
    lock and the retention are set by two calls, so this command has the
    console's own failure mode and has to assert its own result.
    """
    say = progress or (lambda _message: None)
    notes: list[str] = []

    validate_bucket_name(bucket_name)

    say("Inspecting the provisioning credential…")
    auth = authorize(credential, transport)
    refuse_provisioning_credential(auth)

    say(f"Looking for the bucket {bucket_name}…")
    bucket = find_bucket(auth, bucket_name, transport)
    created_bucket = False
    if bucket is None:
        say(f"Creating {bucket_name} with Object Lock…")
        bucket = create_bucket(auth, bucket_name, transport)
        created_bucket = True
    else:
        say(f"Adopting the existing bucket {bucket_name}…")

    state = bucket.lock.state()
    if state is LockState.UNREADABLE:
        raise B2Error(
            "This credential may not read the bucket's Object Lock configuration, so pless "
            "cannot confirm what it is about to rely on. Mint a provisioning credential that "
            "includes readBucketRetentions.",
            script=bootstrap_key_script(auth.account_id),
        )
    if state is LockState.DISABLED:
        raise B2Error(
            f"The bucket {bucket_name!r} exists without Object Lock, and Backblaze does not "
            "allow it to be added afterwards. This bucket can never protect versions from a "
            "compromised machine. Use a different name; if it is empty, deleting it costs "
            "nothing."
        )
    if state is LockState.COMPLIANCE:
        raise B2Error(
            f"The bucket {bucket_name!r} is in compliance mode, which binds you as much as an "
            "attacker — nothing can be deleted before expiry by anyone, and Backblaze's remedy "
            "for a period set too long is closing the account. pless will not try to change "
            "the mode, because whether that is even permitted is unverified and a repair that "
            "half succeeds is worse than a refusal. Use a different bucket; if this one is "
            "empty, deleting it costs nothing."
        )

    repaired_retention = False
    if not retention_satisfies(bucket.lock, retention_days):
        if state is LockState.ENABLED_WITHOUT_RETENTION:
            say(f"Setting a governance retention of {retention_days} days…")
        else:
            say(f"Raising the governance retention to {retention_days} days…")
        bucket = set_governance_retention(auth, bucket, retention_days, transport)
        repaired_retention = True

    if not created_bucket and count_objects(auth, bucket, transport):
        notes.append(
            "This bucket already held objects. A default retention applies only to what is "
            "written after it is set, so those are not covered by it."
        )

    say("Checking for an existing machine key…")
    existing = existing_machine_keys(auth, bucket, transport)
    if existing and not allow_new_key:
        raise B2Error(
            f"This bucket already has {len(existing)} key(s) restricted to it: "
            f"{', '.join(existing)}. pless will not mint another by default, because a target "
            "may be using one. Pass --new-key if you want an additional key — it does not "
            "remove the old ones."
        )
    if existing:
        notes.append(
            f"The previous key(s) ({', '.join(existing)}) still work. Delete them in the "
            "Backblaze console once the target is using the new one — pless does not, because "
            "a running backup may still depend on them."
        )

    say("Minting the machine key…")
    machine_key = mint_machine_key(auth, bucket, transport)

    # The lock and the retention were set by two calls, so the state that
    # matters is the one Backblaze reports now, not the one it accepted.
    say("Re-reading the bucket to confirm what Backblaze actually stored…")
    final = find_bucket(auth, bucket_name, transport)
    if final is None:
        raise B2Error(
            f"The bucket {bucket_name!r} could not be read back after provisioning, so pless "
            "cannot confirm it is protected."
        )
    if not retention_satisfies(final.lock, retention_days):
        raise B2Error(
            f"After provisioning, {bucket_name!r} does not report a governance retention of at "
            f"least {retention_days} days — it reports {final.lock.state()}. The machine key "
            f"{machine_key.key_id} was created and is valid, but the bucket does not protect "
            "what it writes. Run this again to repair the retention."
        )

    if auth.key_expires_at:
        notes.append(
            f"The provisioning credential itself expires at {auth.key_expires_at}. That does "
            "not affect the machine key, which was minted without an expiry."
        )

    return ProvisionOutcome(
        bucket=final,
        machine_key=machine_key,
        repository=repository_for(auth.s3_api_url, bucket_name),
        notes=notes,
        created_bucket=created_bucket,
        repaired_retention=repaired_retention,
    )
