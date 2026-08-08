"""Backup: everything that can be decided without a machine.

The pipeline itself is a shell script rendered from configuration, so what is
tested here is the rendering, the parsing and the bookkeeping — which is where
the decisions live.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pless import backup, composegen, config


def a_config(**backup_overrides) -> config.Config:
    cfg = config.Config()
    cfg.backup = config.BackupConfig(**backup_overrides)
    return cfg


def some_secrets(**overrides) -> config.Secrets:
    values = {
        "restic_password": "9K2M4-XR7TQ-B8HNV-5WGDC-3PFJZ",
        "b2_account_id": "id-0001",
        "b2_account_key": "key-0001",
    }
    values.update(overrides)
    return config.Secrets(**values)


class TestRepositoryKind:
    @pytest.mark.parametrize(
        ("repository", "expected"),
        [
            ("", "unset"),
            ("b2:archive-bucket:paperless", "b2"),
            ("s3:s3.example.com/archive", "s3"),
            ("rclone:dropbox:paperless", "rclone"),
            ("sftp:user@host:/srv/restic", "sftp"),
            ("/mnt/backup/restic", "local"),
            ("./backups/restic", "local"),
            ("backups/restic", "local"),
        ],
    )
    def test_kind_is_derived_not_configured(self, repository: str, expected: str) -> None:
        assert a_config(restic_repository=repository).backup.repository_kind == expected

    def test_local_is_recognised_so_the_docs_can_be_blunt_about_it(self) -> None:
        assert a_config(restic_repository="/mnt/backup").backup.is_local_repository
        assert not a_config(restic_repository="b2:bucket:path").backup.is_local_repository

    def test_empty_repository_disables_backup(self) -> None:
        assert not a_config().backup.is_configured
        assert a_config(restic_repository="/mnt/backup").backup.is_configured


class TestRenderBackupEnv:
    def test_names_everything_that_is_missing_at_once(self) -> None:
        with pytest.raises(backup.BackupError) as exc:
            backup.render_backup_env(a_config(), config.Secrets(restic_password=""))
        message = str(exc.value)
        assert "restic_repository" in message
        assert "RESTIC_PASSWORD" in message

    def test_b2_repository_requires_b2_credentials(self) -> None:
        cfg = a_config(restic_repository="b2:bucket:paperless")
        with pytest.raises(backup.BackupError, match="B2_ACCOUNT_ID"):
            backup.render_backup_env(cfg, some_secrets(b2_account_id=""))

    def test_local_repository_needs_no_b2_credentials(self) -> None:
        cfg = a_config(restic_repository="/mnt/backup/restic")
        rendered = backup.render_backup_env(cfg, some_secrets(b2_account_id="", b2_account_key=""))
        assert "B2_ACCOUNT_ID" not in rendered

    def test_points_at_the_documented_way_to_get_a_passphrase(self) -> None:
        with pytest.raises(backup.BackupError, match="pless secrets generate"):
            backup.render_backup_env(a_config(), config.Secrets())

    def test_values_are_quoted_so_a_shell_cannot_reinterpret_them(self) -> None:
        cfg = a_config(restic_repository="/mnt/backup")
        rendered = backup.render_backup_env(cfg, some_secrets(restic_password="a b'c$d`e"))
        assert "RESTIC_PASSWORD='a b'\\''c$d`e'" in rendered

    def test_exports_what_restic_reads(self) -> None:
        cfg = a_config(restic_repository="b2:bucket:paperless")
        rendered = backup.render_backup_env(cfg, some_secrets())
        assert "export RESTIC_REPOSITORY RESTIC_PASSWORD" in rendered
        assert "export B2_ACCOUNT_ID B2_ACCOUNT_KEY" in rendered


class TestRenderScript:
    def test_is_a_pure_function_of_configuration(self) -> None:
        cfg = a_config(restic_repository="/mnt/backup")
        assert backup.render_script(cfg) == backup.render_script(cfg)

    def test_carries_no_secret(self) -> None:
        cfg = a_config(restic_repository="b2:bucket:paperless")
        script = backup.render_script(cfg)
        assert "9K2M4" not in script
        assert "RESTIC_PASSWORD=" not in script
        assert backup.ENV_FILE in script  # sourced, never inlined

    def test_a_locked_volume_is_a_skip_not_a_failure(self) -> None:
        script = backup.render_script(a_config())
        assert "mountpoint -q" in script
        assert 'exit "$EXIT_SKIPPED"' in script
        assert f"EXIT_SKIPPED={backup.EXIT_SKIPPED}" in script

    def test_writes_a_failed_record_before_doing_any_work(self) -> None:
        # A run interrupted by a power cut must read as failed, never as absent.
        script = backup.render_script(a_config())
        first_record = script.index("write_record failed")
        dump = script.index("pg_dump")
        assert first_record < dump

    def test_dumps_the_database_before_exporting(self) -> None:
        script = backup.render_script(a_config())
        assert script.index("pg_dump") < script.index("document_exporter")

    def test_honours_the_quiescence_timeout(self) -> None:
        script = backup.render_script(a_config(quiescence_timeout_seconds=42))
        assert "QUIESCENCE_TIMEOUT=42" in script

    def test_never_deletes_unless_asked(self) -> None:
        assert "--delete" not in backup.render_script(a_config())
        assert "--delete" in backup.render_script(a_config(exporter_delete=True))

    def test_tags_snapshots_so_retention_can_find_them(self) -> None:
        assert f"--tag {backup.SNAPSHOT_TAG}" in backup.render_script(a_config())

    def test_the_database_password_is_actually_checked(self) -> None:
        # The Postgres image trusts local socket connections by default. Relying
        # on that means the backup works until someone hardens the image, so the
        # dump connects over TCP where the password is verified.
        script = backup.render_script(a_config())
        assert "-h 127.0.0.1" in script

    def test_the_database_password_never_reaches_argv(self) -> None:
        # It comes from the container's own environment, not from a command line
        # any local user could read with ps (ADR 0014).
        script = backup.render_script(a_config())
        assert 'PGPASSWORD="$POSTGRES_PASSWORD"' in script

    def test_documents_are_counted_by_matching_not_by_excluding(self) -> None:
        # --split-manifest writes "<stem>-manifest.json" beside each document.
        # Counting those is stable; excluding the top-level files we happen to
        # know about would miscount as soon as Paperless adds another.
        script = backup.render_script(a_config())
        assert "-name '*-manifest.json'" in script


class TestTheRenderedShellIsValid:
    """Generated shell that does not parse is a backup that never runs.

    Unit tests over strings cannot see a missing `fi`. `sh -n` can, and it costs
    nothing to ask it.
    """

    @pytest.mark.parametrize("exporter_delete", [False, True])
    def test_the_script_parses(self, tmp_path: Path, exporter_delete: bool) -> None:
        script = tmp_path / "pless-backup.sh"
        script.write_text(backup.render_script(a_config(exporter_delete=exporter_delete)))
        result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("sample_size", [1, 20])
    def test_the_verify_script_parses(self, tmp_path: Path, sample_size: int) -> None:
        script = tmp_path / "pless-backup-verify.sh"
        script.write_text(backup.render_verify_script(a_config(verify_sample_size=sample_size)))
        result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_the_environment_file_sources_cleanly(self, tmp_path: Path) -> None:
        env_file = tmp_path / "backup.env"
        cfg = a_config(restic_repository="b2:bucket:paperless")
        env_file.write_text(backup.render_backup_env(cfg, some_secrets()))
        result = subprocess.run(["sh", "-n", str(env_file)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_an_awkward_passphrase_survives_the_round_trip(self, tmp_path: Path) -> None:
        # A user-chosen passphrase may hold a space, a quote, a dollar and a
        # backtick. Any of those unquoted turns the file into a shell injection.
        awkward = "a b'c$d`e\\f"
        env_file = tmp_path / "backup.env"
        cfg = a_config(restic_repository="/mnt/backup")
        env_file.write_text(backup.render_backup_env(cfg, some_secrets(restic_password=awkward)))
        result = subprocess.run(
            ["sh", "-c", f". {env_file}; printf '%s' \"$RESTIC_PASSWORD\""],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == awkward


class TestRenderUnits:
    def test_renders_a_service_and_a_timer_for_backup_and_for_verification(self) -> None:
        units = backup.render_units(a_config())
        assert set(units) == {
            backup.SERVICE_UNIT,
            backup.TIMER_UNIT,
            backup.VERIFY_SERVICE_UNIT,
            backup.VERIFY_TIMER_UNIT,
        }

    def test_verification_runs_on_its_own_schedule(self) -> None:
        units = backup.render_units(a_config(verify_schedule="Sun *-*-* 05:00:00"))
        assert "OnCalendar=Sun *-*-* 05:00:00" in units[backup.VERIFY_TIMER_UNIT]

    def test_a_locked_volume_skips_verification_too(self) -> None:
        units = backup.render_units(a_config())
        assert f"SuccessExitStatus={backup.EXIT_SKIPPED}" in units[backup.VERIFY_SERVICE_UNIT]

    def test_a_skip_counts_as_success(self) -> None:
        units = backup.render_units(a_config())
        assert f"SuccessExitStatus={backup.EXIT_SKIPPED}" in units[backup.SERVICE_UNIT]

    def test_the_unit_does_not_depend_on_the_mount(self) -> None:
        # RequiresMountsFor would turn "the volume is locked" — the normal state
        # after every reboot — into a dependency failure, which is exactly the
        # alarm this design exists to avoid. The script checks the mount itself.
        units = backup.render_units(a_config())
        assert "RequiresMountsFor" not in units[backup.SERVICE_UNIT]

    def test_the_unit_runs_a_script_that_survives_a_locked_volume(self) -> None:
        # Found by drilling on a VM: with the script on the encrypted volume,
        # sh exits 2 before SuccessExitStatus can apply, and the unit lands in
        # 'failed' after every single reboot.
        units = backup.render_units(a_config())
        assert not backup.SCRIPT_PATH.startswith(composegen.INSTALL_DIR)
        assert f"ExecStart=/bin/sh {backup.SCRIPT_PATH}" in units[backup.SERVICE_UNIT]

    def test_the_environment_file_stays_on_the_encrypted_volume(self) -> None:
        # It holds RESTIC_PASSWORD, so it must be unreadable while locked.
        assert backup.ENV_FILE.startswith(composegen.INSTALL_DIR)

    def test_the_schedule_is_configurable(self) -> None:
        units = backup.render_units(a_config(schedule="Mon *-*-* 03:00:00"))
        assert "OnCalendar=Mon *-*-* 03:00:00" in units[backup.TIMER_UNIT]

    def test_a_missed_schedule_is_caught_up(self) -> None:
        units = backup.render_units(a_config())
        assert "Persistent=true" in units[backup.TIMER_UNIT]

    def test_carries_no_secret(self) -> None:
        for content in backup.render_units(a_config()).values():
            assert "PASSWORD" not in content


class TestParseQueueState:
    def test_an_empty_queue_is_drained(self) -> None:
        state = backup.parse_queue_state("##PENDING\n0\n##ACTIVE\n- empty -\n")
        assert state.is_drained

    def test_pending_work_is_not_drained(self) -> None:
        state = backup.parse_queue_state("##PENDING\n3\n##ACTIVE\n- empty -\n")
        assert not state.is_drained
        assert state.pending == 3

    def test_running_tasks_are_counted(self) -> None:
        output = (
            "##PENDING\n0\n"
            "##ACTIVE\n-> celery@worker: OK\n"
            "* {'id': 'abc', 'name': 'consume_file'}\n"
        )
        state = backup.parse_queue_state(output)
        assert state.active == 1
        assert not state.is_drained

    def test_unknown_is_never_treated_as_drained(self) -> None:
        # Guessing here would produce a quietly inconsistent export.
        state = backup.parse_queue_state("##PENDING\nunknown\n##ACTIVE\n")
        assert state.pending is None
        assert not state.is_drained

    def test_missing_output_entirely_is_not_drained(self) -> None:
        assert not backup.parse_queue_state("").is_drained


class TestParseSnapshots:
    SAMPLE = json.dumps(
        [
            {
                "id": "aaaa1111",
                "time": "2026-08-01T02:00:00Z",
                "hostname": "archive-01",
                "tags": ["pless"],
                "paths": ["/opt/paperless/export"],
            },
            {
                "id": "bbbb2222",
                "time": "2026-08-05T02:00:00Z",
                "hostname": "archive-01",
                "tags": ["pless"],
            },
            {"id": "cccc3333", "time": "2026-08-06T02:00:00Z", "tags": ["manual"]},
        ]
    )

    def test_newest_first(self) -> None:
        snapshots = backup.parse_snapshots(self.SAMPLE)
        assert [s.id for s in snapshots] == ["cccc3333", "bbbb2222", "aaaa1111"]

    def test_latest_ignores_snapshots_we_did_not_write(self) -> None:
        latest = backup.latest_snapshot(backup.parse_snapshots(self.SAMPLE))
        assert latest is not None
        assert latest.id == "bbbb2222"

    def test_an_empty_repository_yields_nothing(self) -> None:
        assert backup.parse_snapshots("[]") == []
        assert backup.parse_snapshots("") == []
        assert backup.latest_snapshot([]) is None

    def test_unreadable_output_raises_rather_than_looking_empty(self) -> None:
        # An empty repository and a broken restic must never look alike.
        with pytest.raises(backup.BackupError):
            backup.parse_snapshots("not json")

    def test_tolerates_fields_restic_may_add(self) -> None:
        snapshots = backup.parse_snapshots('[{"id": "a", "time": "t", "programme": "x"}]')
        assert snapshots[0].id == "a"


class TestBackupRun:
    def test_reads_a_successful_record(self) -> None:
        record = backup.BackupRun.from_json(
            json.dumps(
                {
                    "started_at": "2026-08-06T02:00:00Z",
                    "finished_at": "2026-08-06T02:04:00Z",
                    "outcome": "succeeded",
                    "snapshot_id": "abcd1234",
                    "documents_exported": 4182,
                    "queue_moved_during_run": False,
                    "detail": "",
                }
            )
        )
        assert record.succeeded
        assert record.documents_exported == 4182

    def test_an_unknown_outcome_is_read_as_failed(self) -> None:
        record = backup.BackupRun.from_json('{"outcome": "probably fine"}')
        assert record.outcome is backup.RunOutcome.FAILED

    def test_a_missing_outcome_is_read_as_failed(self) -> None:
        assert not backup.BackupRun.from_json("{}").succeeded

    def test_malformed_json_raises_rather_than_reading_as_absent(self) -> None:
        with pytest.raises(backup.BackupError, match="not valid JSON"):
            backup.BackupRun.from_json("{oh dear")


class TestRetentionPolicy:
    def test_takes_its_numbers_from_configuration(self) -> None:
        cfg = a_config(retention_daily=3, retention_weekly=2, retention_monthly=1)
        policy = backup.RetentionPolicy.from_config(cfg)
        args = policy.forget_args()
        assert "--keep-daily" in args
        assert args[args.index("--keep-daily") + 1] == "3"
        assert args[args.index("--keep-weekly") + 1] == "2"

    def test_only_touches_snapshots_we_wrote(self) -> None:
        args = backup.RetentionPolicy.from_config(a_config()).forget_args()
        assert args[args.index("--tag") + 1] == backup.SNAPSHOT_TAG


class TestVerificationRecord:
    def test_reads_a_passing_record(self) -> None:
        record = backup.VerificationRecord.from_json(
            json.dumps(
                {
                    "level": "content",
                    "performed_at": "2026-08-06T02:14:07Z",
                    "snapshot_id": "9f2c1ab4",
                    "repository_kind": "b2",
                    "passed": True,
                    "documents_expected": 4182,
                    "documents_found": 4182,
                    "sample_size": 20,
                    "mismatches": [],
                    "detail": "all matched",
                }
            )
        )
        assert record.passed
        assert record.level is backup.VerificationLevel.CONTENT
        assert record.documents_found == 4182

    def test_malformed_json_raises_rather_than_reading_as_absent(self) -> None:
        with pytest.raises(backup.BackupError, match="not valid JSON"):
            backup.VerificationRecord.from_json("{oh dear")

    def test_an_unknown_level_falls_back_to_content(self) -> None:
        record = backup.VerificationRecord.from_json('{"level": "vibes"}')
        assert record.level is backup.VerificationLevel.CONTENT

    def test_a_missing_passed_field_is_not_a_pass(self) -> None:
        assert not backup.VerificationRecord.from_json("{}").passed


class TestStaleness:
    NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    def record(self, performed_at: str) -> backup.VerificationRecord:
        return backup.VerificationRecord(performed_at=performed_at, passed=True)

    def test_a_recent_record_is_fresh(self) -> None:
        assert not self.record("2026-08-19T12:00:00Z").is_stale(14, self.NOW)

    def test_an_old_record_is_stale(self) -> None:
        # A restore proved months ago proves little about a repository that has
        # been written to every day since.
        assert self.record("2026-06-01T12:00:00Z").is_stale(14, self.NOW)

    def test_the_boundary_is_the_configured_window(self) -> None:
        assert not self.record("2026-08-06T12:00:00Z").is_stale(14, self.NOW)
        assert self.record("2026-08-06T11:00:00Z").is_stale(14, self.NOW)

    def test_a_missing_timestamp_counts_as_stale(self) -> None:
        # The point of the record is to answer "recently?". One that cannot say
        # when is not evidence.
        assert self.record("").is_stale(14, self.NOW)

    def test_an_unreadable_timestamp_counts_as_stale(self) -> None:
        assert self.record("last Tuesday").is_stale(14, self.NOW)

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        assert not self.record("2026-08-19T12:00:00").is_stale(14, self.NOW)


class TestRenderVerifyScript:
    def test_is_a_pure_function_of_configuration(self) -> None:
        cfg = a_config(restic_repository="/mnt/backup")
        assert backup.render_verify_script(cfg) == backup.render_verify_script(cfg)

    def test_carries_no_secret(self) -> None:
        script = backup.render_verify_script(a_config(restic_repository="b2:bucket:paperless"))
        assert "RESTIC_PASSWORD=" not in script
        assert backup.ENV_FILE in script

    def test_honours_the_configured_sample_size(self) -> None:
        assert "SAMPLE_SIZE=7" in backup.render_verify_script(a_config(verify_sample_size=7))

    def test_a_locked_volume_is_a_skip(self) -> None:
        script = backup.render_verify_script(a_config())
        assert "mountpoint -q" in script
        assert 'exit "$EXIT_SKIPPED"' in script

    def test_an_empty_repository_fails_rather_than_passing_quietly(self) -> None:
        # A repository that was silently recreated looks exactly like success.
        script = backup.render_verify_script(a_config())
        assert "recreated" in script

    def test_counts_against_the_run_record_not_the_live_archive(self) -> None:
        # The natural time to verify is right after a backup, when the source
        # has legitimately moved on.
        script = backup.render_verify_script(a_config())
        assert "documents_exported" in script
        assert backup.RUN_RECORD in script

    def test_removes_its_scratch_directory(self) -> None:
        script = backup.render_verify_script(a_config())
        assert 'rm -rf "$SCRATCH"' in script

    def test_restores_a_sample_rather_than_the_archive(self) -> None:
        script = backup.render_verify_script(a_config())
        assert "shuf -n" in script

    def test_sampled_paths_survive_spaces_in_filenames(self) -> None:
        """Paperless filenames contain spaces — that is the normal case.

        Found on a VM: building `--include=` arguments into a string and
        expanding it unquoted split every filename in half, and restic read the
        fragments as extra snapshot IDs. `sh -n` parses that happily, so only
        real filenames reveal it. Positional parameters keep the spaces.
        """
        script = backup.render_verify_script(a_config())
        assert 'set -- "$@" --include "$sample_path"' in script
        assert '--target "$SCRATCH" "$@"' in script
        assert "--include=" not in script  # the form that split on spaces


class TestS3RepositoryCredentials:
    """An s3: repository needs credentials too, under different names.

    Missed until a B2 bucket was being set up by hand: the env file emitted
    credentials only for `b2:` repositories, so an `s3:` one — the endpoint
    Object Lock actually works through — would have authenticated with nothing.
    """

    def test_an_s3_repository_gets_aws_style_credentials(self) -> None:
        cfg = a_config(restic_repository="s3:https://s3.eu-central-003.backblazeb2.com/archive")
        rendered = backup.render_backup_env(cfg, some_secrets())
        assert "AWS_ACCESS_KEY_ID='id-0001'" in rendered
        assert "AWS_SECRET_ACCESS_KEY='key-0001'" in rendered
        assert "export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY" in rendered

    def test_a_b2_repository_still_gets_b2_style_credentials(self) -> None:
        rendered = backup.render_backup_env(
            a_config(restic_repository="b2:bucket:x"), some_secrets()
        )
        assert "B2_ACCOUNT_ID='id-0001'" in rendered
        assert "AWS_ACCESS_KEY_ID" not in rendered

    def test_an_s3_repository_without_credentials_is_refused(self) -> None:
        cfg = a_config(restic_repository="s3:https://s3.example.com/archive")
        with pytest.raises(backup.BackupError, match="B2_ACCOUNT_ID"):
            backup.render_backup_env(cfg, some_secrets(b2_account_id=""))

    def test_the_error_says_which_value_b2_calls_it(self) -> None:
        # "B2_ACCOUNT_ID" is restic's name; the console shows "keyID".
        cfg = a_config(restic_repository="s3:https://s3.example.com/archive")
        with pytest.raises(backup.BackupError, match="keyID"):
            backup.render_backup_env(cfg, some_secrets(b2_account_id=""))

    def test_a_local_repository_still_needs_no_credentials(self) -> None:
        rendered = backup.render_backup_env(
            a_config(restic_repository="/mnt/backup"),
            some_secrets(b2_account_id="", b2_account_key=""),
        )
        assert "AWS_ACCESS_KEY_ID" not in rendered and "B2_ACCOUNT_ID" not in rendered
