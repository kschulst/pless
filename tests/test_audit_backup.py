"""The backup half of the exposure audit.

These checks answer one question — can the machine's own credential destroy the
history that is supposed to survive it — and the five-state lock table is where
getting it wrong is dangerous rather than merely wrong. Conflate "not allowed
to look" with "no lock" and the audit either cries wolf on a correct bucket
every time, or certifies a destroyable one by silence.

They reuse `b2`'s parsers deliberately: two implementations of that
classification is how the two drift, and a drifted copy is the failure this
exists to catch.
"""

from __future__ import annotations

import json
import re

import pytest
from b2_payloads import (
    AUTHORIZE_V3,
    LOCK_COMPLIANCE,
    LOCK_GOVERNANCE_90,
    LOCK_OFF,
    LOCK_ON_NO_RETENTION,
    LOCK_WITHHELD,
    governance_lock,
)

from pless import audit, b2, config

MACHINE_CAPABILITIES = list(b2.MACHINE_KEY_CAPABILITIES)


def a_machine_key(capabilities: list[str] | None = None, **overrides) -> b2.Authorization:
    values = {
        "account_id": "f434a75d3f3c",
        "capabilities": capabilities if capabilities is not None else MACHINE_CAPABILITIES,
        "bucket_id": "bucket-1",
        "bucket_name": "pless-archive",
        "s3_api_url": "https://s3.eu-central-003.backblazeb2.com",
    }
    values.update(overrides)
    return b2.Authorization(**values)


class TestBackupInstalled:
    def test_both_timers_enabled_passes(self) -> None:
        finding = audit.check_backup_installed(
            ["restic: present", "backup-timer: enabled", "verify-timer: enabled"]
        )
        assert finding.ok

    def test_missing_restic_is_critical(self) -> None:
        finding = audit.check_backup_installed(["restic: missing"])

        assert not finding.ok
        assert finding.severity is audit.Severity.CRITICAL
        assert "pless bootstrap" in finding.detail

    def test_a_disabled_timer_is_a_warning_that_names_it(self) -> None:
        finding = audit.check_backup_installed(
            ["restic: present", "backup-timer: enabled", "verify-timer: disabled"]
        )

        assert not finding.ok
        assert finding.severity is audit.Severity.WARNING
        assert "verify-timer: disabled" in finding.detail
        assert "pless backup init" in finding.detail


class TestBackupCredential:
    def test_a_correctly_scoped_key_passes(self) -> None:
        finding = audit.check_backup_credential(a_machine_key())

        assert finding.ok
        assert "pless-archive" in finding.detail

    @pytest.mark.parametrize("capability", audit.FORBIDDEN_ON_THE_TARGET)
    def test_every_forbidden_capability_is_critical(self, capability: str) -> None:
        finding = audit.check_backup_credential(a_machine_key([*MACHINE_CAPABILITIES, capability]))

        assert not finding.ok
        assert finding.severity is audit.Severity.CRITICAL
        assert capability in finding.detail

    def test_bypass_governance_gets_its_own_sentence(self) -> None:
        """It is not one of several; it is the one that makes the rest moot."""
        finding = audit.check_backup_credential(
            a_machine_key([*MACHINE_CAPABILITIES, "bypassGovernance"])
        )

        assert "defeats Object Lock outright" in finding.detail

    def test_an_unrestricted_key_is_critical(self) -> None:
        finding = audit.check_backup_credential(a_machine_key(bucket_id="", bucket_name=""))

        assert not finding.ok
        assert "every bucket in the account" in finding.detail

    def test_a_key_without_read_bucket_retentions_is_critical(self) -> None:
        """Then the bucket check cannot run, and the operator is trusting rather
        than checking (ADR 0020)."""
        finding = audit.check_backup_credential(
            a_machine_key([c for c in MACHINE_CAPABILITIES if c != "readBucketRetentions"])
        )

        assert not finding.ok
        assert "readBucketRetentions" in finding.detail
        assert "pless b2 provision" in finding.detail

    def test_an_expiring_key_is_a_warning_that_names_the_date(self) -> None:
        """Backups would start failing on a day nobody chose, quietly."""
        finding = audit.check_backup_credential(
            a_machine_key(key_expires_at="2027-03-01T00:00:00Z")
        )

        assert not finding.ok
        assert finding.severity is audit.Severity.WARNING
        assert "2027-03-01T00:00:00Z" in finding.detail

    def test_a_dangerous_capability_outranks_an_expiry(self) -> None:
        finding = audit.check_backup_credential(
            a_machine_key(
                [*MACHINE_CAPABILITIES, "bypassGovernance"],
                key_expires_at="2027-03-01T00:00:00Z",
            )
        )

        assert finding.severity is audit.Severity.CRITICAL


class TestBucketProtection:
    """Five states. Conflating any two of them is the bug."""

    def _lock(self, payload: dict) -> b2.LockConfiguration:
        return b2.parse_lock_configuration(payload)

    def test_governance_long_enough_passes(self) -> None:
        finding = audit.check_bucket_protection(self._lock(LOCK_GOVERNANCE_90), 90)

        assert finding.ok
        assert "90 days" in finding.detail

    def test_withheld_is_critical_and_says_it_is_not_a_verdict(self) -> None:
        finding = audit.check_bucket_protection(self._lock(LOCK_WITHHELD), 90)

        assert not finding.ok
        assert "withheld" in finding.detail
        assert "not the same as the bucket being" in finding.detail

    def test_no_object_lock_is_critical_and_says_a_new_bucket_is_needed(self) -> None:
        finding = audit.check_bucket_protection(self._lock(LOCK_OFF), 90)

        assert not finding.ok
        assert finding.severity is audit.Severity.CRITICAL
        assert "cannot be added to an existing bucket" in finding.detail

    def test_lock_on_with_no_retention_is_critical(self) -> None:
        """The dangerous state: it looks like protection and is none."""
        finding = audit.check_bucket_protection(self._lock(LOCK_ON_NO_RETENTION), 90)

        assert not finding.ok
        assert finding.severity is audit.Severity.CRITICAL
        assert "looks like protection and is none" in finding.detail

    def test_lock_on_with_no_retention_is_not_confused_with_no_lock(self) -> None:
        """A check asking only `isFileLockEnabled` would pass the first of these."""
        on_no_retention = audit.check_bucket_protection(self._lock(LOCK_ON_NO_RETENTION), 90)
        no_lock = audit.check_bucket_protection(self._lock(LOCK_OFF), 90)

        assert on_no_retention.detail != no_lock.detail

    def test_compliance_is_a_warning_not_a_pass(self) -> None:
        finding = audit.check_bucket_protection(self._lock(LOCK_COMPLIANCE), 30)

        assert not finding.ok
        assert finding.severity is audit.Severity.WARNING
        assert "binds you as much as an attacker" in finding.detail

    def test_a_period_that_is_too_short_is_critical(self) -> None:
        finding = audit.check_bucket_protection(self._lock(LOCK_GOVERNANCE_90), 180)

        assert not finding.ok
        assert "90 days" in finding.detail
        assert "180" in finding.detail

    def test_years_are_converted_before_comparing(self) -> None:
        finding = audit.check_bucket_protection(self._lock(governance_lock(1, "years")), 90)

        assert finding.ok
        assert "365 days" in finding.detail

    def test_an_unrecognised_unit_fails_rather_than_being_assumed(self) -> None:
        """Assuming days would silently pass a bucket configured otherwise."""
        finding = audit.check_bucket_protection(self._lock(governance_lock(90, "fortnights")), 90)

        assert not finding.ok
        assert "does not recognise" in finding.detail

    def test_versioning_is_never_accepted_as_evidence(self) -> None:
        """ADR 0017 rejected it: lifecycle rules do not stop explicit deletion."""
        payload = dict(LOCK_OFF)
        payload["lifecycleRules"] = [{"daysFromHidingToDeleting": 90}]
        payload["bucketType"] = "allPrivate"

        assert not audit.check_bucket_protection(self._lock(payload), 90).ok


class TestRepositoryEndpoint:
    ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"

    def test_a_matching_endpoint_passes(self) -> None:
        finding = audit.check_repository_endpoint(
            f"s3:{self.ENDPOINT}/pless-archive", self.ENDPOINT
        )
        assert finding.ok

    def test_another_account_is_a_warning(self) -> None:
        finding = audit.check_repository_endpoint(
            "s3:https://s3.us-west-001.backblazeb2.com/pless-archive", self.ENDPOINT
        )

        assert not finding.ok
        assert finding.severity is audit.Severity.WARNING
        assert "different account" in finding.detail

    @pytest.mark.parametrize(
        ("repository", "endpoint"),
        [("/mnt/backup/restic", ENDPOINT), ("sftp:host:/srv/restic", ENDPOINT), ("s3:x", "")],
    )
    def test_nothing_to_compare_passes_quietly(self, repository: str, endpoint: str) -> None:
        assert audit.check_repository_endpoint(repository, endpoint).ok


def a_config(repository: str = "s3:https://s3.eu-central-003.backblazeb2.com/pless-archive"):
    cfg = config.Config()
    cfg.backup = config.BackupConfig(restic_repository=repository, version_retention_days=90)
    return cfg


def collected(backup_lines: str = "", auth: dict | None = None, bucket: dict | None = None) -> str:
    """What one SSH round trip comes back with."""
    parts = ["##LISTEN", "##UFW", "##DOCKER", "##SSHD", "##MOUNT", "##BACKUP", backup_lines]
    parts += ["##B2AUTH", json.dumps(auth) if auth else ""]
    parts += ["##B2BUCKET", json.dumps(bucket) if bucket else ""]
    return "\n".join(parts)


class TestAnalyse:
    HEALTHY_BACKUP = "restic: present\nbackup-timer: enabled\nverify-timer: enabled"

    def test_without_a_config_nothing_backup_related_runs(self) -> None:
        """`cfg` stays optional so every existing caller is unaffected."""
        report = audit.analyse(collected(self.HEALTHY_BACKUP))

        assert not any("backup" in f.check or "bucket" in f.check for f in report.findings)

    def test_an_unconfigured_repository_adds_nothing(self) -> None:
        report = audit.analyse(collected(self.HEALTHY_BACKUP), a_config(""))

        assert not any("bucket" in f.check for f in report.findings)

    def test_a_full_collection_produces_every_backup_finding(self) -> None:
        report = audit.analyse(
            collected(
                self.HEALTHY_BACKUP,
                auth=AUTHORIZE_V3,
                bucket={"buckets": [{"bucketName": "pless-archive", **LOCK_GOVERNANCE_90}]},
            ),
            a_config(),
        )
        checks = {f.check for f in report.findings}

        assert {"backup installed", "backup credential", "bucket protection"} <= checks

    def test_an_unparseable_authorization_is_not_a_pass(self) -> None:
        """An unverifiable credential is not a passing one."""
        output = collected(self.HEALTHY_BACKUP).replace("##B2AUTH\n", "##B2AUTH\nnot json at all\n")
        report = audit.analyse(output, a_config())
        credential = next(f for f in report.findings if f.check == "backup credential")

        assert not credential.ok
        assert "could not be checked" in credential.detail

    def test_a_missing_bucket_answer_reads_as_withheld_not_as_absent(self) -> None:
        """Silence is never a pass here."""
        report = audit.analyse(collected(self.HEALTHY_BACKUP, auth=AUTHORIZE_V3), a_config())
        protection = next(f for f in report.findings if f.check == "bucket protection")

        assert not protection.ok

    def test_the_exposure_findings_are_still_there(self) -> None:
        report = audit.analyse(collected(self.HEALTHY_BACKUP), a_config())
        checks = {f.check for f in report.findings}

        assert {"encrypted storage", "firewall", "listening sockets"} & checks


class TestTheCollectScript:
    def test_it_is_valid_posix_shell(self) -> None:
        """Three earlier versions were valid shell that said the wrong thing, so
        this is necessary and nowhere near sufficient."""
        import subprocess

        completed = subprocess.run(
            ["sh", "-n"], input=audit.COLLECT_SCRIPT, text=True, capture_output=True
        )
        assert completed.returncode == 0, completed.stderr

    def test_the_credential_never_reaches_argv(self) -> None:
        """It is sourced from the environment file, which is the whole point."""
        script = audit.COLLECT_SCRIPT

        assert f". {b2.API_URL}" not in script
        assert "backup.env" in script
        assert '-u "$B2_ID:$B2_KEY"' in script

    def test_curl_cannot_hang_past_the_ssh_timeout(self) -> None:
        """The audit's round trip is bounded at 120s and this makes two calls."""
        assert audit.COLLECT_SCRIPT.count("--max-time") == 2

    def test_the_json_body_it_builds_is_valid(self) -> None:
        """An earlier version lost its escaped quotes to Python and sent
        `{accountId:VALUE}`, which `sh -n` accepted without complaint."""
        assert '{\\"accountId\\":\\"$ACCOUNT\\",\\"bucketId\\":\\"$BUCKET\\"}' in (
            audit.COLLECT_SCRIPT
        )

    def test_it_asks_for_both_credential_spellings(self) -> None:
        """ADR 0017 requires the S3 endpoint, so the scheme is `s3:` while the
        credentials are still a B2 key — `render_backup_env` emits AWS names."""
        assert "AWS_ACCESS_KEY_ID" in audit.COLLECT_SCRIPT
        assert "B2_ACCOUNT_ID" in audit.COLLECT_SCRIPT

    def test_the_markers_it_emits_are_the_ones_analyse_looks_for(self) -> None:
        """A marker the script writes and nothing reads is a check that silently
        never runs."""
        emitted = {
            match.group(1).lower()
            for match in re.finditer(r'echo "##([A-Z0-9]+)"', audit.COLLECT_SCRIPT)
        }

        assert {"backup", "b2auth", "b2bucket"} <= emitted
