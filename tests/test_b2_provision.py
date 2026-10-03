"""Provisioning, as a sequence.

What matters here is not that each call works but that they happen in the right
order and that the refusals happen *before* anything exists. "Nothing was
created" is the assertion, not an afterthought: a refusal that fires after the
bucket is made is not a refusal.

The transport double records every call, so order is asserted directly rather
than inferred. `TestTheFakes` holds it to the `Transport` protocol — a fake
with the wrong shape is a test that passes while the code breaks.
"""

from __future__ import annotations

import inspect

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

from pless import b2

CREDENTIAL = b2.ProvisioningCredential(key_id="003prov", application_key="K003PROVSECRET")

MINTED_KEY = {
    "applicationKeyId": "003f434a75d3f3c0000000009",
    "applicationKey": "K003MACHINESECRET",
    "capabilities": list(b2.MACHINE_KEY_CAPABILITIES),
    "bucketId": "bucket-1",
    "keyName": b2.MACHINE_KEY_NAME,
}


def a_bucket(name: str = "pless-archive", lock: dict | None = None) -> dict:
    payload = {"bucketId": "bucket-1", "bucketName": name}
    payload.update(lock or LOCK_ON_NO_RETENTION)
    return payload


class Recorder:
    """A transport that answers by endpoint and remembers the order."""

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.answers: dict[str, object] = answers or {}
        self.calls: list[str] = []
        self.bodies: list[dict | None] = []

    def __call__(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        credential: b2.ProvisioningCredential | str | None = None,
    ) -> tuple[int, dict]:
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        self.bodies.append(body)
        answer = self.answers.get(endpoint, (200, {}))
        if callable(answer):
            answer = answer(len([c for c in self.calls if c == endpoint]))
        return answer  # type: ignore[return-value]

    def ran(self, endpoint: str) -> bool:
        return endpoint in self.calls

    def index_of(self, endpoint: str) -> int:
        return self.calls.index(endpoint)

    def body_for(self, endpoint: str) -> dict:
        return self.bodies[self.index_of(endpoint)] or {}


def a_working_account(
    *,
    existing_bucket: dict | None = None,
    final_lock: dict | None = None,
    keys: list[dict] | None = None,
    files: list[dict] | None = None,
) -> Recorder:
    """Every call succeeds. Individual tests break exactly one of them."""
    listings = [
        {"buckets": [existing_bucket]} if existing_bucket else {"buckets": []},
        {"buckets": [a_bucket(lock=final_lock or LOCK_GOVERNANCE_90)]},
    ]

    def list_buckets(call_number: int) -> tuple[int, dict]:
        return 200, listings[min(call_number - 1, len(listings) - 1)]

    return Recorder(
        {
            "b2_authorize_account": (200, AUTHORIZE_V3),
            "b2_list_buckets": list_buckets,
            "b2_create_bucket": (200, a_bucket()),
            "b2_update_bucket": (200, a_bucket(lock=LOCK_GOVERNANCE_90)),
            "b2_list_file_versions": (200, {"files": files or []}),
            "b2_list_keys": (200, {"keys": keys or []}),
            "b2_create_key": (200, MINTED_KEY),
        }
    )


def provision(transport: Recorder, **kwargs) -> b2.ProvisionOutcome:
    options = {"bucket_name": "pless-archive", "retention_days": 90}
    options.update(kwargs)
    return b2.provision(CREDENTIAL, transport=transport, **options)  # type: ignore[arg-type]


class TestTheHappyPathOnANewBucket:
    def test_it_creates_sets_retention_and_mints(self) -> None:
        transport = a_working_account()
        outcome = provision(transport)

        assert outcome.created_bucket
        assert outcome.machine_key.key_id == MINTED_KEY["applicationKeyId"]
        assert outcome.repository == ("s3:https://s3.eu-central-003.backblazeb2.com/pless-archive")

    def test_the_credential_is_inspected_before_anything_is_created(self) -> None:
        transport = a_working_account()
        provision(transport)

        assert transport.calls[0] == "b2_authorize_account"

    def test_listing_precedes_creating(self) -> None:
        """Without this ordering, duplicate_bucket_name cannot say whose it is."""
        transport = a_working_account()
        provision(transport)

        assert transport.index_of("b2_list_buckets") < transport.index_of("b2_create_bucket")

    def test_the_retention_call_follows_the_create_call(self) -> None:
        transport = a_working_account()
        provision(transport)

        assert transport.index_of("b2_create_bucket") < transport.index_of("b2_update_bucket")

    def test_object_lock_is_requested_at_creation(self) -> None:
        """It cannot be added afterwards, so there is no second chance."""
        transport = a_working_account()
        provision(transport)

        assert transport.body_for("b2_create_bucket")["fileLockEnabled"] is True
        assert transport.body_for("b2_create_bucket")["bucketType"] == "allPrivate"

    def test_the_retention_is_governance_from_configuration(self) -> None:
        transport = a_working_account()
        provision(transport, retention_days=30)

        retention = transport.body_for("b2_update_bucket")["defaultRetention"]
        assert retention["mode"] == "governance"
        assert retention["period"] == {"duration": 30, "unit": "days"}

    def test_the_minted_key_asks_for_exactly_the_six_and_no_expiry(self) -> None:
        transport = a_working_account()
        provision(transport)

        body = transport.body_for("b2_create_key")
        assert sorted(body["capabilities"]) == sorted(b2.MACHINE_KEY_CAPABILITIES)
        assert "validDurationInSeconds" not in body
        assert body["bucketId"] == "bucket-1"

    def test_the_bucket_is_read_back_before_success_is_claimed(self) -> None:
        """The lock and the retention are two calls, so the state that matters
        is the one Backblaze reports afterwards."""
        transport = a_working_account()
        provision(transport)

        assert transport.calls.count("b2_list_buckets") == 2
        assert transport.calls[-1] == "b2_list_buckets"

    def test_progress_reaches_the_caller_without_rich(self) -> None:
        transport = a_working_account()
        steps: list[str] = []
        provision(transport, progress=steps.append)

        assert steps
        assert any("Inspecting" in step for step in steps)


class TestRefusalsThatCreateNothing:
    def test_a_bad_bucket_name_never_reaches_the_network(self) -> None:
        transport = a_working_account()
        with pytest.raises(b2.B2Error, match="6 to 50"):
            provision(transport, bucket_name="short")

        assert transport.calls == []

    def test_a_credential_holding_bypass_governance_stops_after_authorize(self) -> None:
        """The refusal that makes the whole feature worth building."""
        over_powered = dict(AUTHORIZE_V3)
        storage = dict(over_powered["apiInfo"]["storageApi"])  # type: ignore[index]
        storage["capabilities"] = [*storage["capabilities"], "bypassGovernance"]
        over_powered["apiInfo"] = {"storageApi": storage}

        transport = a_working_account()
        transport.answers["b2_authorize_account"] = (200, over_powered)

        with pytest.raises(b2.B2Error, match="bypassGovernance"):
            provision(transport)

        assert transport.calls == ["b2_authorize_account"], "it acted on a rejected credential"

    def test_an_uninspectable_credential_stops_everything(self) -> None:
        transport = a_working_account()
        transport.answers["b2_authorize_account"] = (401, {"code": "unauthorized"})

        with pytest.raises(b2.B2Error, match="could not be inspected"):
            provision(transport)

        assert transport.calls == ["b2_authorize_account"]

    def test_a_bucket_restricted_credential_is_refused_before_creating(self) -> None:
        restricted = dict(AUTHORIZE_V3)
        restricted["apiInfo"] = {
            "storageApi": {
                **AUTHORIZE_V3["apiInfo"]["storageApi"],  # type: ignore[index]
                "bucketId": "other-bucket",
                "bucketName": "someone-elses",
            }
        }
        transport = a_working_account()
        transport.answers["b2_authorize_account"] = (200, restricted)

        with pytest.raises(b2.B2Error, match="cannot create another one"):
            provision(transport)

        assert not transport.ran("b2_create_bucket")

    def test_a_bucket_without_object_lock_is_unrepairable(self) -> None:
        """The lock cannot be added later, so this one can never be made safe."""
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_OFF))

        with pytest.raises(b2.B2Error, match="does not allow it to be added"):
            provision(transport)

        assert not transport.ran("b2_update_bucket")
        assert not transport.ran("b2_create_key")

    def test_a_compliance_bucket_is_refused_not_repaired(self) -> None:
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_COMPLIANCE))

        with pytest.raises(b2.B2Error, match="compliance mode"):
            provision(transport)

        assert not transport.ran("b2_update_bucket"), "it tried to change the mode"
        assert not transport.ran("b2_create_key")

    def test_an_unreadable_lock_stops_rather_than_guesses(self) -> None:
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_WITHHELD))

        with pytest.raises(b2.B2Error, match="may not read"):
            provision(transport)

        assert not transport.ran("b2_create_key")

    def test_a_name_held_by_another_account_says_so(self) -> None:
        """find_bucket already established this account does not hold it."""
        transport = a_working_account()
        transport.answers["b2_create_bucket"] = (400, {"code": b2.DUPLICATE_BUCKET_CODE})

        with pytest.raises(b2.B2Error, match="taken by another Backblaze account"):
            provision(transport)

        assert not transport.ran("b2_create_key")

    def test_a_key_returned_with_the_wrong_rights_fails(self) -> None:
        """Trusting that the request was honoured would mean trusting the thing
        this command exists because nobody should trust."""
        transport = a_working_account()
        transport.answers["b2_create_key"] = (
            200,
            {**MINTED_KEY, "capabilities": [*b2.MACHINE_KEY_CAPABILITIES, "bypassGovernance"]},
        )

        with pytest.raises(b2.B2Error, match="capabilities pless did not ask for"):
            provision(transport)


class TestAdoptingAnExistingBucket:
    def test_a_missing_retention_is_repaired(self) -> None:
        """This is provision's own failure mode, not only the console's."""
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_ON_NO_RETENTION))
        outcome = provision(transport)

        assert not outcome.created_bucket
        assert outcome.repaired_retention
        assert not transport.ran("b2_create_bucket")
        assert transport.ran("b2_update_bucket")

    def test_a_retention_that_is_too_short_is_raised(self) -> None:
        transport = a_working_account(
            existing_bucket=a_bucket(lock=LOCK_GOVERNANCE_90),
            final_lock=governance_lock(180),
        )
        outcome = provision(transport, retention_days=180)

        assert outcome.repaired_retention
        assert transport.body_for("b2_update_bucket")["defaultRetention"]["period"] == {
            "duration": 180,
            "unit": "days",
        }

    def test_an_adequate_retention_is_left_alone(self) -> None:
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_GOVERNANCE_90))
        outcome = provision(transport, retention_days=30)

        assert not outcome.repaired_retention
        assert not transport.ran("b2_update_bucket"), "it lowered an adequate retention"

    def test_objects_already_in_the_bucket_are_called_out(self) -> None:
        """A default retention covers only what is written after it is set."""
        transport = a_working_account(
            existing_bucket=a_bucket(lock=LOCK_ON_NO_RETENTION),
            files=[{"fileName": "already-here.txt"}],
        )
        outcome = provision(transport)

        assert any("already held objects" in note for note in outcome.notes)

    def test_the_object_check_never_enumerates(self) -> None:
        transport = a_working_account(existing_bucket=a_bucket(lock=LOCK_ON_NO_RETENTION))
        provision(transport)

        assert transport.body_for("b2_list_file_versions")["maxFileCount"] == 1


class TestExistingMachineKeys:
    EXISTING = [{"applicationKeyId": "003old", "bucketId": "bucket-1"}]

    def test_an_existing_key_stops_the_command(self) -> None:
        transport = a_working_account(keys=self.EXISTING)

        with pytest.raises(b2.B2Error, match="--new-key"):
            provision(transport)

        assert not transport.ran("b2_create_key")

    def test_new_key_mints_an_additional_one(self) -> None:
        transport = a_working_account(keys=self.EXISTING)
        outcome = provision(transport, allow_new_key=True)

        assert transport.ran("b2_create_key")
        assert any("still work" in note for note in outcome.notes)

    def test_nothing_is_ever_deleted(self) -> None:
        """A target may be using the old key; pless does not break it."""
        transport = a_working_account(keys=self.EXISTING)
        provision(transport, allow_new_key=True)

        assert not any("delete" in call for call in transport.calls)

    def test_keys_for_other_buckets_are_ignored(self) -> None:
        transport = a_working_account(
            keys=[{"applicationKeyId": "003elsewhere", "bucketId": "another-bucket"}]
        )
        provision(transport)

        assert transport.ran("b2_create_key")


class TestVerifyBeforeReporting:
    def test_a_final_state_without_retention_fails_despite_every_step_succeeding(self) -> None:
        """Closes provision's own two-call window."""
        transport = a_working_account(final_lock=LOCK_ON_NO_RETENTION)

        with pytest.raises(b2.B2Error, match="does not report a governance retention"):
            provision(transport)

    def test_the_failure_names_the_key_that_was_created(self) -> None:
        """It is valid and the operator needs to know it exists."""
        transport = a_working_account(final_lock=LOCK_ON_NO_RETENTION)

        with pytest.raises(b2.B2Error) as exc:
            provision(transport)

        assert MINTED_KEY["applicationKeyId"] in str(exc.value)

    def test_a_bucket_that_vanishes_is_not_reported_as_protected(self) -> None:
        transport = a_working_account()
        transport.answers["b2_list_buckets"] = lambda call: (
            (200, {"buckets": []}) if call == 1 else (200, {"buckets": []})
        )

        with pytest.raises(b2.B2Error, match="could not be read back"):
            provision(transport)

    def test_success_reports_the_state_actually_read_back(self) -> None:
        transport = a_working_account()
        outcome = provision(transport)

        assert outcome.bucket.lock.state() is b2.LockState.GOVERNANCE


class TestSecretsNeverLeave:
    def test_no_error_message_carries_the_credential(self) -> None:
        transport = a_working_account()
        transport.answers["b2_authorize_account"] = (401, {"code": "unauthorized"})

        with pytest.raises(b2.B2Error) as exc:
            provision(transport)

        assert CREDENTIAL.application_key not in str(exc.value)

    def test_no_error_message_carries_the_bearer_token(self) -> None:
        transport = a_working_account()
        transport.answers["b2_create_bucket"] = (500, {"code": "internal_error"})

        with pytest.raises(b2.B2Error) as exc:
            provision(transport)

        assert "SECRET" not in str(exc.value)

    def test_the_credential_is_sent_only_to_authorize(self) -> None:
        """Everything else uses the bearer token."""
        sent: list[object] = []
        base = a_working_account()

        def recording(method, url, body=None, credential=None):
            sent.append((url.rsplit("/", 1)[-1], credential))
            return base(method, url, body, credential)

        b2.provision(CREDENTIAL, "pless-archive", 90, recording)  # type: ignore[arg-type]

        for endpoint, credential in sent:
            if endpoint == "b2_authorize_account":
                assert isinstance(credential, b2.ProvisioningCredential)
            else:
                assert not isinstance(credential, b2.ProvisioningCredential)


class TestTheFakes:
    """A fake with the wrong signature is a test that passes while code breaks."""

    def test_the_recorder_matches_the_transport_protocol(self) -> None:
        protocol = inspect.signature(b2.Transport.__call__)
        recorder = inspect.signature(Recorder.__call__)

        assert list(protocol.parameters) == list(recorder.parameters)

    def test_the_recorder_returns_what_the_protocol_promises(self) -> None:
        status, payload = Recorder()("GET", "https://example.invalid/b2_authorize_account")

        assert isinstance(status, int)
        assert isinstance(payload, dict)
