"""Restore: the half of backup that only matters on the worst day.

`backup.restore` and the full rehearsal both talk to machines, so what is
tested here is the wiring and the refusals — which is where the damage would
be. The fakes carry the same signatures as the functions they stand in for,
and `TestTheFakes` holds them to it: the day `sshexec.run` changes shape again
must not be a day these tests keep passing while restore breaks.
"""

from __future__ import annotations

import pytest

from pless import backup, config, sshexec, storage
from pless.targets import Host

A_HOST = Host(label="arkiv-01", ssh_args=["-p", "22", "deploy@nowhere.invalid"])


def a_config(**backup_overrides) -> config.Config:
    cfg = config.Config()
    cfg.backup = config.BackupConfig(**backup_overrides)
    return cfg


class FakeSsh:
    """Answers remote commands by substring, and remembers the order they came in."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.commands: list[str] = []

    def run(
        self,
        destination: list[str],
        remote_command: str,
        timeout: int = 60,
        input_text: str | None = None,
    ) -> sshexec.SshResult:
        self.commands.append(remote_command)
        for fragment, (code, out) in self.answers.items():
            if fragment in remote_command:
                return sshexec.SshResult(exit_code=code, stdout=out, stderr="" if not code else out)
        return sshexec.SshResult(exit_code=0, stdout="", stderr="")

    def ran(self, fragment: str) -> bool:
        return any(fragment in command for command in self.commands)

    def index_of(self, fragment: str) -> int:
        for position, command in enumerate(self.commands):
            if fragment in command:
                return position
        raise AssertionError(f"No command contained {fragment!r}: {self.commands}")


def a_mounted_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        storage,
        "status",
        lambda cfg, target: storage.StorageStatus(
            device="/dev/sdb", is_luks=True, is_open=True, is_mounted=True
        ),
    )


class TestSnapshotIdIsValidatedNotQuoted:
    """Everything reaching the target goes through a single-quoted `sh -c`."""

    @pytest.mark.parametrize(
        "snapshot_id",
        ["'; rm -rf / #", "latest; restic forget --prune", "$(id)", "../../etc", ""],
    )
    def test_refuses_anything_that_is_not_a_snapshot_id(
        self, snapshot_id: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="not a snapshot id"):
            backup.restore(a_config(restic_repository="b2:b:p"), A_HOST, snapshot_id)
        assert fake.commands == [], "it reached the machine before validating"

    @pytest.mark.parametrize("snapshot_id", ["latest", "42e78445", "A1B2C3D4E5F6"])
    def test_accepts_latest_and_hex_ids(self, snapshot_id: str) -> None:
        assert backup.SNAPSHOT_ID_PATTERN.match(snapshot_id)


class TestDocumentCount:
    def test_reads_the_count_from_the_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeSsh({"documents_document": (0, "17\n")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        assert backup.document_count(A_HOST) == 17

    def test_a_failing_query_is_not_an_empty_archive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The dangerous misreading: "no answer" must never mean "no documents"."""
        fake = FakeSsh({"documents_document": (1, "could not connect to server")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="how many documents"):
            backup.document_count(A_HOST)

    def test_unparseable_output_is_not_an_empty_archive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSsh({"documents_document": (0, "no such table\n")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="Refusing to treat that"):
            backup.document_count(A_HOST)

    def test_the_password_stays_out_of_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADR 0014: it is read from the container's environment, not passed in."""
        fake = FakeSsh({"documents_document": (0, "0\n")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        backup.document_count(A_HOST)
        assert 'PGPASSWORD="$POSTGRES_PASSWORD"' in fake.commands[0]


class TestRestoreRefusals:
    def test_refuses_a_target_that_already_holds_documents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_mounted_volume(monkeypatch)
        fake = FakeSsh({"documents_document": (0, "43\n")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="already holds 43 documents"):
            backup.restore(a_config(restic_repository="b2:b:p"), A_HOST)
        assert not fake.ran("restic restore"), "it restored over a live archive"
        assert not fake.ran("document_importer")

    def test_refuses_a_locked_volume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            storage,
            "status",
            lambda cfg, target: storage.StorageStatus(
                device="/dev/sdb", is_luks=True, is_open=False, is_mounted=False
            ),
        )
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="not mounted"):
            backup.restore(a_config(restic_repository="b2:b:p"), A_HOST)
        assert not fake.ran("restic restore")


class TestRestoreHappyPath:
    def test_restores_then_imports_and_returns_the_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_mounted_volume(monkeypatch)
        counts = iter(["0\n", "31\n"])  # empty before, populated after
        fake = FakeSsh()
        fake.answers = {"documents_document": (0, "")}

        def answer(destination, remote_command, timeout=60, input_text=None):
            fake.commands.append(remote_command)
            if "documents_document" in remote_command:
                return sshexec.SshResult(exit_code=0, stdout=next(counts), stderr="")
            return sshexec.SshResult(exit_code=0, stdout="", stderr="")

        monkeypatch.setattr(sshexec, "run", answer)
        imported = backup.restore(a_config(restic_repository="b2:b:p"), A_HOST, "42e78445")

        assert imported == 31
        assert fake.index_of("restic restore") < fake.index_of("document_importer")
        assert "--target /" in fake.commands[fake.index_of("restic restore")]
        assert f". {backup.ENV_FILE}" in fake.commands[fake.index_of("restic restore")]


class TestVerificationRecordRoundTrip:
    def test_a_written_record_reads_back_the_same(self) -> None:
        record = backup.VerificationRecord(
            level=backup.VerificationLevel.FULL,
            performed_at="2026-09-04T10:00:00Z",
            snapshot_id="42e784456f",
            repository_kind="b2",
            passed=True,
            documents_expected=31,
            documents_found=31,
            sample_size=31,
            detail="A new machine produced 31 documents.",
        )
        assert backup.VerificationRecord.from_json(record.to_json()) == record

    def test_the_record_names_a_kind_and_never_a_location(self) -> None:
        """A bucket name must not end up in a file that gets pasted into an issue."""
        record = backup.VerificationRecord(repository_kind="b2", snapshot_id="abc")
        assert "b2" in record.to_json()
        assert "bucket" not in record.to_json()


class TestInstallEnvironment:
    def test_writes_credentials_without_scheduling_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rehearsal machine reads the repository; it must not back up to it."""
        a_mounted_volume(monkeypatch)
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        secrets = config.Secrets(restic_password="x", b2_key_id="i", b2_application_key="k")
        backup.install_environment(a_config(restic_repository="b2:bucket:path"), secrets, A_HOST)

        assert fake.ran(backup.ENV_FILE)
        assert not fake.ran("systemctl enable")
        assert not fake.ran(backup.TIMER_UNIT)
