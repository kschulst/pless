"""The escape hatches: getting documents out, and thinning history.

`extract` is the only command that puts readable documents on an unencrypted
disk, and `forget` is the only one that removes history. Both are deliberate
acts, so what is tested here is the refusals, the exact commands that reach the
machine, and — for `extract` — that the plumbing cannot deadlock or swallow a
failure.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from ssh_doubles import FakeSsh

from pless import backup, config, sshexec
from pless.targets import Host

A_HOST = Host(label="arkiv-01", ssh_args=["-p", "22", "deploy@nowhere.invalid"])


def a_config(repository: str = "s3:https://s3.eu-central-003.example.com/archive") -> config.Config:
    cfg = config.Config()
    cfg.backup = config.BackupConfig(restic_repository=repository)
    return cfg


class TestRepositoryLabel:
    """What the confirmation prompt asks the operator to type."""

    @pytest.mark.parametrize(
        ("repository", "expected"),
        [
            ("b2:archive-bucket:paperless", "archive-bucket"),
            ("s3:https://s3.eu-central-003.backblazeb2.com/pless-tesst", "pless-tesst"),
            ("s3:s3.example.com/archive", "archive"),
            ("/mnt/backup/restic", "restic"),
            ("./backups/restic/", "restic"),
            ("sftp:deploy@host:/srv/restic", "restic"),
            ("rclone:dropbox:paperless", "paperless"),
            ("", ""),
        ],
    )
    def test_names_the_thing_being_pruned(self, repository: str, expected: str) -> None:
        assert backup.repository_label(repository) == expected

    def test_never_asks_the_operator_to_retype_an_endpoint(self) -> None:
        """A prompt that wants a URL retyped teaches pasting, not reading."""
        label = backup.repository_label("s3:https://s3.eu-central-003.backblazeb2.com/archive")
        assert "/" not in label and "https" not in label


class FakeStream:
    """Stands in for Popen: carries a tar on stdout and an exit code."""

    def __init__(self, tar_bytes: bytes = b"", returncode: int = 0) -> None:
        self.stdout = io.BytesIO(tar_bytes)
        self.returncode = returncode
        self.waited = False

    def wait(self) -> int:
        self.waited = True
        return self.returncode


class Recorder:
    """Captures what extract would actually run."""

    def __init__(self, stream_code: int = 0, tar_code: int = 0, stderr: bytes = b"") -> None:
        self.ssh_argv: list[str] = []
        self.tar_argv: list[str] = []
        self.stream = FakeStream(returncode=stream_code)
        self.tar_code = tar_code
        self.stderr = stderr

    def popen(self, argv, stdout=None, stderr=None):
        self.ssh_argv = list(argv)
        if self.stderr and stderr is not None:
            stderr.write(self.stderr)
        return self.stream

    def run(self, argv, stdin=None, capture_output=False, text=False, check=False):
        self.tar_argv = list(argv)
        return subprocess.CompletedProcess(argv, self.tar_code, stdout="", stderr="tar: broken")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Recorder:
        monkeypatch.setattr(subprocess, "Popen", self.popen)
        monkeypatch.setattr(subprocess, "run", self.run)
        return self


class TestExtractRefusals:
    def test_refuses_when_no_repository_is_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder().install(monkeypatch)
        with pytest.raises(backup.BackupError, match="no snapshot to extract from"):
            backup.extract(a_config(""), A_HOST, tmp_path)
        assert recorder.ssh_argv == []

    @pytest.mark.parametrize("snapshot_id", ["'; rm -rf / #", "$(id)", ""])
    def test_refuses_anything_that_is_not_a_snapshot_id(
        self, snapshot_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder().install(monkeypatch)
        with pytest.raises(backup.BackupError, match="not a snapshot id"):
            backup.extract(a_config(), A_HOST, tmp_path, snapshot_id)
        assert recorder.ssh_argv == [], "it reached the machine before validating"


class TestExtractPlumbing:
    def test_streams_a_tar_and_never_stages_plaintext_on_the_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = Recorder().install(monkeypatch)
        backup.extract(a_config(), A_HOST, tmp_path / "out", "42e78445")

        remote = recorder.ssh_argv[-1]
        assert "restic dump --archive tar 42e78445" in remote
        assert backup.EXPORT_DIR in remote
        assert f". {backup.ENV_FILE}" in remote
        # Nothing restores into a directory on the target first.
        assert "restic restore" not in remote
        assert "--target" not in remote

    def test_leaves_out_thumbnails_and_both_kinds_of_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Readable files, not half an export that looks re-importable.

        The export holds a manifest beside each document *and* a top-level pair
        that indexes the whole thing. A drill found the second pair surviving.
        """
        recorder = Recorder().install(monkeypatch)
        backup.extract(a_config(), A_HOST, tmp_path)

        for pattern in (
            "*-manifest.json",
            "*-thumbnail.webp",
            "*/manifest.json",
            "*/metadata.json",
        ):
            assert f"--exclude={pattern}" in recorder.tar_argv

    def test_the_documents_land_in_the_destination_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """restic dump writes absolute paths, so the prefix has to come off.

        Without this the operator gets `<destination>/opt/paperless/export/…`
        and has to go digging, while the command claims it wrote to the
        destination.
        """
        recorder = Recorder().install(monkeypatch)
        backup.extract(a_config(), A_HOST, tmp_path)

        assert f"--strip-components={backup.EXPORT_PATH_DEPTH}" in recorder.tar_argv

    def test_the_strip_depth_matches_the_export_path(self) -> None:
        """Derived from the constant, so renaming the export cannot desync it."""
        assert backup.EXPORT_DIR.strip("/").count("/") + 1 == backup.EXPORT_PATH_DEPTH

    def test_creates_the_destination_and_returns_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Recorder().install(monkeypatch)
        destination = tmp_path / "nested" / "documents"
        assert backup.extract(a_config(), A_HOST, destination) == destination
        assert destination.is_dir()

    def test_a_failing_remote_command_is_reported_with_its_own_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Recorder(stream_code=1, stderr=b"Fatal: wrong password").install(monkeypatch)
        with pytest.raises(backup.BackupError, match="wrong password"):
            backup.extract(a_config(), A_HOST, tmp_path)

    def test_a_failing_unpack_is_not_reported_as_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Recorder(tar_code=2).install(monkeypatch)
        with pytest.raises(backup.BackupError, match="Unpacking into"):
            backup.extract(a_config(), A_HOST, tmp_path)

    def test_the_remote_process_is_always_reaped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stream nobody waits for is a zombie and an unreported failure."""
        recorder = Recorder().install(monkeypatch)
        backup.extract(a_config(), A_HOST, tmp_path)
        assert recorder.stream.waited


class TestForget:
    def _command(self, monkeypatch: pytest.MonkeyPatch, **kwargs) -> str:
        seen: list[str] = []

        def answer(destination, remote_command, timeout=60, input_text=None):
            seen.append(remote_command)
            return sshexec.SshResult(exit_code=0, stdout="kept 7 snapshots", stderr="")

        monkeypatch.setattr(sshexec, "run", answer)
        backup.forget(a_config(), A_HOST, **kwargs)
        return seen[0]

    def test_a_dry_run_removes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        command = self._command(monkeypatch, dry_run=True)
        assert "--dry-run" in command
        assert "--prune" not in command

    def test_pruning_is_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        command = self._command(monkeypatch, dry_run=False)
        assert "--prune" in command
        assert "--dry-run" not in command

    def test_dry_run_is_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert "--dry-run" in self._command(monkeypatch)

    def test_it_carries_the_configured_policy_and_only_pless_snapshots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = self._command(monkeypatch)
        for fragment in (
            "--keep-daily 7",
            "--keep-weekly 8",
            "--keep-monthly 12",
            "--keep-yearly 3",
            f"--tag {backup.SNAPSHOT_TAG}",
        ):
            assert fragment in command

    def test_the_policy_comes_from_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            sshexec,
            "run",
            lambda destination, remote_command, timeout=60, input_text=None: (
                seen.append(remote_command) or sshexec.SshResult(0, "", "")
            ),
        )
        cfg = a_config()
        cfg.backup.retention_daily = 30
        backup.forget(cfg, A_HOST)
        assert "--keep-daily 30" in seen[0]


class TestResticVersion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("restic 0.17.3 compiled with go1.23.1 on linux/arm64", (0, 17, 3)),
            ("restic 0.16.4 compiled with go1.22.2 on linux/amd64", (0, 16, 4)),
            ("restic 0.14.0", (0, 14, 0)),
            ("restic 1.0", (1, 0, 0)),
        ],
    )
    def test_the_version_is_read_not_guessed(self, text: str, expected: tuple) -> None:
        assert backup.parse_restic_version(text) == expected

    @pytest.mark.parametrize("text", ["", "command not found", "0.17.3"])
    def test_an_unreadable_version_refuses_rather_than_assuming(self, text: str) -> None:
        """Guessing would mean guessing which way round `restic copy` goes."""
        with pytest.raises(backup.BackupError, match="Refusing to guess"):
            backup.parse_restic_version(text)


class TestMirrorRefusals:
    def _answers(self, version: str = "restic 0.17.3") -> dict:
        return {"restic version": (0, version)}

    def test_an_unconfigured_repository_has_nothing_to_mirror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="no repository to mirror"):
            backup.mirror(a_config(""), A_HOST, "/mnt/mirror")
        assert fake.commands == []

    @pytest.mark.parametrize(
        "destination",
        ["'; rm -rf / #", "/mnt/with space", "$(id)", "a`id`b", "", "sftp:host:/p;ls"],
    )
    def test_a_destination_it_will_not_pass_to_restic_is_refused(
        self, destination: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validated rather than quoted, like a snapshot id."""
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="not a repository location"):
            backup.mirror(a_config(), A_HOST, destination)
        assert fake.commands == [], "it reached the machine before validating"

    @pytest.mark.parametrize(
        "destination",
        [
            "/mnt/backup/mirror",
            "sftp:deploy@elsewhere:/srv/mirror",
            "s3:https://s3.example.com/mirror",
            "rclone:dropbox:paperless",
        ],
    )
    def test_every_repository_form_restic_takes_is_accepted(
        self, destination: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSsh({"restic version": (0, "restic 0.17.3")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        backup.mirror(a_config(), A_HOST, destination)
        assert fake.ran("restic copy")

    def test_mirroring_a_repository_into_itself_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = a_config()
        fake = FakeSsh()
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="into itself"):
            backup.mirror(cfg, A_HOST, cfg.backup.restic_repository)

    def test_restic_older_than_the_direction_change_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Before 0.14 the arguments meant the opposite, and both forms exit zero."""
        fake = FakeSsh({"restic version": (0, "restic 0.13.1")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        with pytest.raises(backup.BackupError, match="other way round"):
            backup.mirror(a_config(), A_HOST, "/mnt/mirror")
        assert not fake.ran("restic copy"), "it copied with an ambiguous direction"


class TestMirrorDirection:
    """Both directions exit zero, so getting it backwards copies the mirror into
    the archive and reports success. This is the test that catches that."""

    def _mirror(self, monkeypatch: pytest.MonkeyPatch, destination: str) -> FakeSsh:
        fake = FakeSsh({"restic version": (0, "restic 0.17.3")})
        monkeypatch.setattr(sshexec, "run", fake.run)
        backup.mirror(a_config(), A_HOST, destination)
        return fake

    def test_the_destination_is_the_repository_and_the_source_is_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        copy = fake.commands[fake.index_of("restic copy")]

        assert "RESTIC_REPOSITORY=/mnt/mirror" in copy
        assert 'RESTIC_FROM_REPOSITORY="$SRC"' in copy

    def test_the_source_is_captured_before_anything_reassigns_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading it from the same command prefix that sets the destination
        would rely on assignments in a prefix not seeing one another."""
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        copy = fake.commands[fake.index_of("restic copy")]

        assert copy.index('SRC="$RESTIC_REPOSITORY"') < copy.index("RESTIC_REPOSITORY=/mnt/mirror")

    def test_both_repositories_use_the_same_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One secret opens both, so the archive restores from either."""
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        copy = fake.commands[fake.index_of("restic copy")]

        assert 'RESTIC_PASSWORD="$PW"' in copy
        assert 'RESTIC_FROM_PASSWORD="$PW"' in copy

    def test_only_pless_snapshots_are_copied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        assert f"--tag {backup.SNAPSHOT_TAG}" in fake.commands[fake.index_of("restic copy")]

    def test_nothing_is_decrypted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`copy` moves packs between repositories; `dump` and `restore` do not."""
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        joined = " ".join(fake.commands)

        assert "restic dump" not in joined
        assert "restic restore" not in joined

    def test_a_fresh_destination_is_initialised_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`restic copy` needs it to exist, and failing there would be a
        confusing place to stop."""
        fake = FakeSsh(
            {"restic version": (0, "restic 0.17.3"), "restic cat config": (1, "no repository")}
        )
        monkeypatch.setattr(sshexec, "run", fake.run)
        backup.mirror(a_config(), A_HOST, "/mnt/mirror")

        assert fake.index_of("restic init") < fake.index_of("restic copy")

    def test_an_existing_destination_is_not_reinitialised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._mirror(monkeypatch, "/mnt/mirror")
        assert not fake.ran("restic init")
