"""B2: everything that can be decided without an account.

The security-relevant judgements in this module are pure functions over parsed
responses, which is the whole point of the split — a refusal that only runs
against a live Backblaze account is a refusal nobody can test.

The payloads here are the ones captured against a real account while #16 was
being settled, not invented shapes. Fakes drifting from what an API actually
returns is a failure mode this project has had before.
"""

from __future__ import annotations

import pytest
from b2_payloads import (
    AUTHORIZE_V2,
    AUTHORIZE_V3,
    LOCK_COMPLIANCE,
    LOCK_GOVERNANCE_90,
    LOCK_OFF,
    LOCK_ON_NO_RETENTION,
    LOCK_WITHHELD,
)

from pless import b2


class TestBucketNameValidation:
    """Checked locally, so a bad name costs nothing and the message names the rule."""

    @pytest.mark.parametrize("name", ["paperless", "pless-archive", "arkiv01", "a1b2c3", "a" * 50])
    def test_names_b2_accepts(self, name: str) -> None:
        b2.validate_bucket_name(name)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("", "required"),
            ("short", "6 to 50"),
            ("a" * 51, "6 to 50"),
            ("b2-archive", "reserves that prefix"),
            ("-archive", "hyphen"),
            ("archive-", "hyphen"),
            ("Paperless", "Lowercase"),
            ("pless_archive", "Lowercase"),
            ("pless archive", "Lowercase"),
        ],
    )
    def test_the_message_names_the_rule_that_was_broken(self, name: str, expected: str) -> None:
        with pytest.raises(b2.B2Error, match=expected):
            b2.validate_bucket_name(name)


class TestParseAuthorization:
    def test_the_v3_shape(self) -> None:
        auth = b2.parse_authorization(AUTHORIZE_V3)

        assert auth.account_id == "f434a75d3f3c"
        assert auth.api_url == "https://api003.backblazeb2.com"
        assert auth.s3_api_url == "https://s3.eu-central-003.backblazeb2.com"
        assert "writeKeys" in auth.capabilities
        assert not auth.is_bucket_restricted

    def test_the_v2_shape_still_parses(self) -> None:
        """v3 nests what v2 did not, and a field observed once is not assumed."""
        auth = b2.parse_authorization(AUTHORIZE_V2)

        assert auth.api_url == "https://api003.backblazeb2.com"
        assert auth.s3_api_url == "https://s3.eu-central-003.backblazeb2.com"
        assert "writeBuckets" in auth.capabilities

    def test_a_bucket_restricted_credential_is_recognised(self) -> None:
        payload = {
            "accountId": "acct",
            "authorizationToken": "t",
            "apiInfo": {"storageApi": {"bucketId": "abc123", "bucketName": "pless-tesst"}},
        }
        auth = b2.parse_authorization(payload)

        assert auth.is_bucket_restricted
        assert auth.bucket_name == "pless-tesst"

    def test_a_missing_token_stops_everything(self) -> None:
        """An unverifiable credential is never assumed to be acceptable."""
        with pytest.raises(b2.B2Error, match="could not be inspected"):
            b2.parse_authorization({"accountId": "acct"})

    def test_an_expiry_is_carried(self) -> None:
        """B2 keys may expire, and backups would then fail on a day nobody chose."""
        payload = dict(AUTHORIZE_V3, applicationKeyExpirationTimestamp="2027-01-01T00:00:00Z")
        assert b2.parse_authorization(payload).key_expires_at == "2027-01-01T00:00:00Z"

    def test_the_token_is_not_rendered(self) -> None:
        auth = b2.parse_authorization(AUTHORIZE_V3)
        assert "SECRET" not in repr(auth)


class TestLockStates:
    """Five states, because conflating any two of them is harmful."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (LOCK_WITHHELD, b2.LockState.UNREADABLE),
            (LOCK_OFF, b2.LockState.DISABLED),
            (LOCK_ON_NO_RETENTION, b2.LockState.ENABLED_WITHOUT_RETENTION),
            (LOCK_COMPLIANCE, b2.LockState.COMPLIANCE),
            (LOCK_GOVERNANCE_90, b2.LockState.GOVERNANCE),
            ({}, b2.LockState.UNREADABLE),
        ],
    )
    def test_classification(self, payload: dict, expected: b2.LockState) -> None:
        assert b2.parse_lock_configuration(payload).state() is expected

    def test_withheld_is_not_the_same_as_absent(self) -> None:
        """B2 returns the field and redacts the value; a check that read that as
        "no lock" would report a correct bucket as broken every time, and one
        that read it as acceptable would certify a destroyable setup."""
        withheld = b2.parse_lock_configuration(LOCK_WITHHELD)
        off = b2.parse_lock_configuration(LOCK_OFF)

        assert withheld.state() is not off.state()
        assert not withheld.authorized_to_read
        assert off.authorized_to_read

    def test_the_dangerous_state_is_not_mistaken_for_success(self) -> None:
        """Lock on, nothing retained — what every bucket looks like between
        creation and the call that sets retention."""
        lock = b2.parse_lock_configuration(LOCK_ON_NO_RETENTION)

        assert lock.enabled, "the lock really is on, which is why this is a trap"
        assert lock.state() is b2.LockState.ENABLED_WITHOUT_RETENTION
        assert not b2.retention_satisfies(lock, 90)

    def test_the_period_is_carried_as_an_object(self) -> None:
        period = b2.parse_lock_configuration(LOCK_GOVERNANCE_90).period
        assert period is not None
        assert (period.duration, period.unit) == (90, "days")


class TestRetentionPeriod:
    @pytest.mark.parametrize(
        ("duration", "unit", "days"), [(90, "days", 90), (1, "years", 365), (2, "years", 730)]
    )
    def test_units_it_recognises(self, duration: int, unit: str, days: int) -> None:
        assert b2.RetentionPeriod(duration, unit).as_days() == days

    @pytest.mark.parametrize("unit", ["hours", "months", "", "DAYS"])
    def test_an_unrecognised_unit_fails_rather_than_being_assumed(self, unit: str) -> None:
        """Assuming days would silently pass a bucket configured otherwise."""
        with pytest.raises(b2.B2Error, match="does not recognise"):
            b2.RetentionPeriod(90, unit).as_days()


class TestRetentionSatisfies:
    @pytest.mark.parametrize(
        ("payload", "required", "expected"),
        [
            (LOCK_GOVERNANCE_90, 90, True),
            (LOCK_GOVERNANCE_90, 30, True),
            (LOCK_GOVERNANCE_90, 180, False),
            (LOCK_COMPLIANCE, 30, False),
            (LOCK_ON_NO_RETENTION, 1, False),
            (LOCK_OFF, 1, False),
            (LOCK_WITHHELD, 1, False),
        ],
    )
    def test_only_governance_long_enough_counts(
        self, payload: dict, required: int, expected: bool
    ) -> None:
        assert b2.retention_satisfies(b2.parse_lock_configuration(payload), required) is expected

    def test_compliance_does_not_satisfy_even_when_long_enough(self) -> None:
        """It protects, and binds the operator too, so it is a finding not a pass."""
        lock = b2.parse_lock_configuration(LOCK_COMPLIANCE)
        assert not b2.retention_satisfies(lock, 1)


class TestRefuseProvisioningCredential:
    def _auth(self, capabilities: list[str], **overrides) -> b2.Authorization:
        values = {"account_id": "acct", "capabilities": capabilities}
        values.update(overrides)
        return b2.Authorization(**values)

    ACCEPTABLE = [*b2.MACHINE_KEY_CAPABILITIES, *b2.REQUIRED_IN_PROVISIONING_KEY]

    def test_an_acceptable_credential_passes(self) -> None:
        b2.refuse_provisioning_credential(self._auth(self.ACCEPTABLE))

    def test_bypass_governance_is_refused(self) -> None:
        """The load-bearing refusal: B2 cannot withhold from a child what the
        parent holds, so this is what makes a dangerous key unobtainable."""
        with pytest.raises(b2.B2Error) as exc:
            b2.refuse_provisioning_credential(self._auth([*self.ACCEPTABLE, "bypassGovernance"]))

        message = str(exc.value)
        assert "bypassGovernance" in message
        assert "withhold what the parent key has" in message

    def test_the_refusal_prints_a_working_call(self) -> None:
        """Prose with placeholders to substitute is where operators go wrong."""
        with pytest.raises(b2.B2Error) as exc:
            b2.refuse_provisioning_credential(self._auth([*self.ACCEPTABLE, "bypassGovernance"]))

        message = str(exc.value)
        assert "b2_create_key" in message
        assert "acct" in message, "the account id is filled in, not left as a placeholder"
        assert "<" not in message.split("Mint one")[1], "nothing left to substitute"

    def test_a_bucket_restricted_credential_is_refused_up_front(self) -> None:
        """Failing at b2_create_bucket instead would be late and obscure."""
        with pytest.raises(b2.B2Error, match="cannot create another one"):
            b2.refuse_provisioning_credential(
                self._auth(self.ACCEPTABLE, bucket_id="abc", bucket_name="pless-tesst")
            )

    @pytest.mark.parametrize("missing", b2.REQUIRED_IN_PROVISIONING_KEY)
    def test_a_credential_missing_what_it_needs_is_refused_by_name(self, missing: str) -> None:
        capabilities = [c for c in self.ACCEPTABLE if c != missing]
        with pytest.raises(b2.B2Error, match=missing):
            b2.refuse_provisioning_credential(self._auth(capabilities))

    def test_the_bootstrap_call_grants_everything_a_machine_key_needs(self) -> None:
        """A key cannot grant capabilities it lacks, so the provisioning key
        must hold the machine key's rights as well as its own."""
        command = b2.bootstrap_key_command("acct")
        for capability in b2.MACHINE_KEY_CAPABILITIES:
            assert capability in command
        for capability in b2.REQUIRED_IN_PROVISIONING_KEY:
            assert capability in command

    def test_the_bootstrap_call_never_grants_bypass_governance(self) -> None:
        assert "bypassGovernance" not in b2.bootstrap_key_command("acct")

    def test_the_bootstrap_call_unsets_the_master_key_afterwards(self) -> None:
        assert "unset" in b2.bootstrap_key_command("acct")


class TestRepositoryFor:
    def test_the_form_adr_0017_specifies(self) -> None:
        assert (
            b2.repository_for("https://s3.eu-central-003.backblazeb2.com", "pless-tesst")
            == "s3:https://s3.eu-central-003.backblazeb2.com/pless-tesst"
        )

    def test_a_trailing_slash_does_not_double(self) -> None:
        assert b2.repository_for("https://s3.example.com/", "archive") == (
            "s3:https://s3.example.com/archive"
        )

    def test_a_missing_endpoint_fails_rather_than_building_nonsense(self) -> None:
        with pytest.raises(b2.B2Error, match="S3 endpoint"):
            b2.repository_for("", "archive")

    def test_the_region_is_never_configured(self) -> None:
        """It comes from the account's own s3ApiUrl, so there is nothing to type."""
        auth = b2.parse_authorization(AUTHORIZE_V3)
        assert "eu-central-003" in b2.repository_for(auth.s3_api_url, "archive")


class TestRenderApiError:
    def test_it_uses_b2s_own_words(self) -> None:
        message = b2.render_api_error(
            401, {"code": "unauthorized", "message": "Invalid credentials"}
        )
        assert "401" in message
        assert "unauthorized" in message
        assert "Invalid credentials" in message

    def test_a_non_json_body_falls_back_to_the_status(self) -> None:
        message = b2.render_api_error(502, None, "<html>Bad Gateway</html>")
        assert "502" in message
        assert "Bad Gateway" in message

    def test_an_empty_answer_says_so(self) -> None:
        assert "no explanation" in b2.render_api_error(500, None, "")

    def test_it_never_renders_a_credential_or_a_token(self) -> None:
        """The realistic leak path is an error pasted into an issue, and this
        project settles arguments by pasting real API output."""
        payload = {
            "code": "bad_request",
            "message": "something failed",
            # A response that echoed these back must not carry them onward.
            "authorizationToken": "4_00acct_SECRET_TOKEN",
            "Authorization": "Basic BASE64CREDENTIAL",
        }
        message = b2.render_api_error(400, payload)

        assert "SECRET_TOKEN" not in message
        assert "BASE64CREDENTIAL" not in message
        assert "Authorization" not in message


class TestSecretsNeverReachARepr:
    """These objects live only in memory, so the leak path is anything rendered."""

    def test_the_provisioning_credential(self) -> None:
        credential = b2.ProvisioningCredential(key_id="003abc", application_key="K003SECRET")

        assert "K003SECRET" not in repr(credential)
        assert "003abc" in repr(credential), "the id is an identifier, not a secret"

    def test_the_machine_key(self) -> None:
        key = b2.MachineKey(key_id="003def", application_key="K003ALSOSECRET")

        assert "K003ALSOSECRET" not in repr(key)
        assert "003def" in repr(key)

    def test_an_outcome_does_not_leak_the_key_through_its_own_repr(self) -> None:
        outcome = b2.ProvisionOutcome(
            bucket=b2.Bucket(bucket_id="b", bucket_name="archive"),
            machine_key=b2.MachineKey(key_id="003def", application_key="K003ALSOSECRET"),
            repository="s3:https://s3.example.com/archive",
        )
        assert "K003ALSOSECRET" not in repr(outcome)


class TestCapabilityLists:
    def test_the_machine_key_holds_exactly_what_the_adrs_specify(self) -> None:
        assert b2.MACHINE_KEY_CAPABILITIES == (
            "deleteFiles",
            "listBuckets",
            "listFiles",
            "readBucketRetentions",
            "readFiles",
            "writeFiles",
        )

    def test_the_machine_key_never_bypasses_governance(self) -> None:
        assert "bypassGovernance" not in b2.MACHINE_KEY_CAPABILITIES

    def test_read_bucket_retentions_is_present_and_write_is_not(self) -> None:
        """ADR 0020: it must verify the lock, and must not be able to change it."""
        assert "readBucketRetentions" in b2.MACHINE_KEY_CAPABILITIES
        assert "writeBucketRetentions" not in b2.MACHINE_KEY_CAPABILITIES
