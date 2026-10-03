"""`pless b2 provision`, driven through the CLI.

Wiring is where drift hides in this project: every module below `cli.py` is
covered by tests over pure functions, and the bug that made `pless preflight`
die before checking anything lived in the wiring rather than in `preflight.py`.

What these mostly hold is that the credential is *prompted* rather than taken
from the command line, that the machine key is disclosed exactly once, and that
a refusal reaches the operator as a message rather than a traceback.
"""

from __future__ import annotations

import inspect

import pytest
from click.testing import Result
from typer.testing import CliRunner

from pless import b2, cli, config

runner = CliRunner()

KEY_ID = "003prov"
APPLICATION_KEY = "K003PROVSECRET"
MACHINE_KEY = "K003MACHINESECRET"


def _configured(path=None) -> config.Config:
    """No [host] at all: provision must work without one."""
    cfg = config.Config()
    cfg.backup = config.BackupConfig(version_retention_days=90)
    return cfg


@pytest.fixture(autouse=True)
def _local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_config", _configured)


def an_outcome(**overrides) -> b2.ProvisionOutcome:
    values = {
        "bucket": b2.Bucket(
            bucket_id="bucket-1",
            bucket_name="pless-archive",
            lock=b2.LockConfiguration(
                authorized_to_read=True,
                enabled=True,
                mode="governance",
                period=b2.RetentionPeriod(90, "days"),
            ),
        ),
        "machine_key": b2.MachineKey(
            key_id="003machine",
            application_key=MACHINE_KEY,
            capabilities=list(b2.MACHINE_KEY_CAPABILITIES),
        ),
        "repository": "s3:https://s3.eu-central-003.backblazeb2.com/pless-archive",
        "created_bucket": True,
    }
    values.update(overrides)
    return b2.ProvisionOutcome(**values)


def invoke(args: list[str], user_input: str | None = None) -> Result:
    return runner.invoke(cli.app, args, input=user_input)


CREDENTIALS = f"{KEY_ID}\n{APPLICATION_KEY}\n"


class TestTheCredentialIsNeverInArgv:
    def test_it_is_prompted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Anything in argv is readable by any local user through `ps`, and this
        credential can create buckets and mint keys."""
        seen: list[b2.ProvisioningCredential] = []

        def provision(credential, bucket_name, retention_days, transport, **kwargs):
            seen.append(credential)
            return an_outcome()

        monkeypatch.setattr(b2, "provision", provision)
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert result.exit_code == 0, result.output
        assert seen[0].key_id == KEY_ID
        assert seen[0].application_key == APPLICATION_KEY

    def test_there_is_no_option_that_would_put_it_in_argv(self) -> None:
        help_text = invoke(["b2", "provision", "--help"]).output
        for forbidden in ("--key", "--application-key", "--credential", "--secret"):
            assert forbidden not in help_text

    def test_the_application_key_is_not_echoed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            b2,
            "provision",
            lambda credential, bucket_name, retention_days, transport, **kwargs: an_outcome(),
        )
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert APPLICATION_KEY not in result.output


class TestWhatItPrintsOnSuccess:
    @pytest.fixture(autouse=True)
    def _succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            b2,
            "provision",
            lambda credential, bucket_name, retention_days, transport, **kwargs: an_outcome(),
        )

    def test_the_repository_line_is_ready_to_paste(self) -> None:
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert (
            'restic_repository = "s3:https://s3.eu-central-003.backblazeb2.com/pless-archive"'
            in result.output
        )

    def test_the_machine_key_is_disclosed_once(self) -> None:
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert result.output.count(MACHINE_KEY) == 1
        assert "only time it is shown" in result.output
        assert "keeps no copy" in result.output

    def test_it_says_what_the_key_can_and_cannot_do(self) -> None:
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert "readBucketRetentions" in result.output
        assert "not bypassGovernance" in result.output

    def test_it_names_the_lock_and_the_period(self) -> None:
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert "Object Lock on" in result.output
        assert "90 days" in result.output

    def test_it_points_at_the_next_command(self) -> None:
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)
        assert "pless backup init" in result.output

    def test_it_works_with_no_host_configured(self) -> None:
        """The only command whose work involves no target at all."""
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)
        assert result.exit_code == 0, result.output


class TestNotesReachTheOperator:
    def test_each_note_is_printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notes = [
            "This bucket already held objects.",
            "The previous key(s) (003old) still work.",
        ]
        monkeypatch.setattr(
            b2,
            "provision",
            lambda credential, bucket_name, retention_days, transport, **kwargs: an_outcome(
                notes=notes, created_bucket=False
            ),
        )
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert "already held objects" in result.output
        assert "003old" in result.output

    def test_a_repair_is_reported_as_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            b2,
            "provision",
            lambda credential, bucket_name, retention_days, transport, **kwargs: an_outcome(
                created_bucket=False, repaired_retention=True
            ),
        )
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert "Adopted" in result.output
        assert "was set" in result.output


class TestRefusalsReachTheOperator:
    def _refusing(self, monkeypatch: pytest.MonkeyPatch, message: str) -> None:
        def refuse(credential, bucket_name, retention_days, transport, **kwargs):
            raise b2.B2Error(message)

        monkeypatch.setattr(b2, "provision", refuse)

    def test_a_refusal_is_a_message_not_a_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._refusing(monkeypatch, "This credential holds bypassGovernance")
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert result.exit_code != 0
        assert "bypassGovernance" in result.output
        assert "Traceback" not in result.output

    def test_the_bootstrap_call_survives_rendering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The refusal's value is a command the operator can paste, so rich must
        not reflow or swallow it."""
        self._refusing(monkeypatch, "Mint one without it:\n\n" + b2.bootstrap_key_command("acct"))
        result = invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert "b2_create_key" in result.output
        assert "readBucketRetentions" in result.output

    def test_a_bad_bucket_name_fails_before_prompting(self) -> None:
        """Nothing should ask for a credential it is not going to use."""
        result = invoke(["b2", "provision", "--bucket", "short"], CREDENTIALS)

        assert result.exit_code != 0


class TestTheBucketIsRequired:
    def test_no_bucket_is_a_usage_error(self) -> None:
        result = invoke(["b2", "provision"], CREDENTIALS)

        assert result.exit_code != 0
        assert "--bucket" in result.output

    def test_new_key_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[bool] = []

        def provision(credential, bucket_name, retention_days, transport, **kwargs):
            seen.append(kwargs.get("allow_new_key", False))
            return an_outcome()

        monkeypatch.setattr(b2, "provision", provision)
        invoke(["b2", "provision", "--bucket", "pless-archive", "--new-key"], CREDENTIALS)

        assert seen == [True]

    def test_the_retention_comes_from_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []

        def provision(credential, bucket_name, retention_days, transport, **kwargs):
            seen.append(retention_days)
            return an_outcome()

        def thirty(path=None) -> config.Config:
            cfg = _configured()
            cfg.backup.version_retention_days = 30
            return cfg

        monkeypatch.setattr(config, "load_config", thirty)
        monkeypatch.setattr(b2, "provision", provision)
        invoke(["b2", "provision", "--bucket", "pless-archive"], CREDENTIALS)

        assert seen == [30]


class TestTheFakes:
    def test_the_fake_takes_what_provision_takes(self) -> None:
        real = list(inspect.signature(b2.provision).parameters)
        fake = list(
            inspect.signature(
                lambda credential, bucket_name, retention_days, transport, **kwargs: None
            ).parameters
        )

        assert real[: len(fake) - 1] == fake[:-1]
        assert fake[-1] == "kwargs"
