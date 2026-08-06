"""Backup: everything that can be decided without a machine.

The pipeline itself is a shell script rendered from configuration, so what is
tested here is the rendering, the parsing and the bookkeeping — which is where
the decisions live.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pless import backup, config


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
    def test_renders_a_service_and_a_timer(self) -> None:
        units = backup.render_units(a_config())
        assert set(units) == {backup.SERVICE_UNIT, backup.TIMER_UNIT}

    def test_the_unit_refuses_to_run_without_the_volume(self) -> None:
        units = backup.render_units(a_config())
        assert "RequiresMountsFor=/opt/paperless" in units[backup.SERVICE_UNIT]

    def test_a_skip_counts_as_success(self) -> None:
        units = backup.render_units(a_config())
        assert f"SuccessExitStatus={backup.EXIT_SKIPPED}" in units[backup.SERVICE_UNIT]

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
