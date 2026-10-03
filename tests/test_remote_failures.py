"""When a target fails, the operator should learn what failed.

Two bugs of the same shape, from opposite directions. A target that accepted
the connection and then stopped answering produced a traceback instead of a
failed result (#13). A registry that refused mid-pull produced systemd's
summary instead of the registry's own words (#19). Both left the operator
holding a message that named nothing — at the moment the machine was least
able to answer for itself.
"""

from __future__ import annotations

import subprocess

import pytest

from pless import backup, config, deploy, sshexec, storage
from pless.targets import Host

A_HOST = Host(label="arkiv-01", ssh_args=["-p", "22", "deploy@nowhere.invalid"])


def _cfg() -> config.Config:
    return config.Config()


def a_hanging_target(monkeypatch: pytest.MonkeyPatch, partial: object = None) -> None:
    """A machine that takes the connection and never answers the command."""

    def hang(argv, capture_output=False, text=False, timeout=None, input=None):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout or 0, output=partial)

    monkeypatch.setattr(subprocess, "run", hang)


class TestATargetThatHangs:
    """#13 — `sshexec.run` let `subprocess.TimeoutExpired` escape."""

    def test_a_timeout_is_a_failed_result_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_hanging_target(monkeypatch)
        result = sshexec.run(A_HOST.ssh_args, "true", timeout=5)

        assert not result.ok
        assert result.timed_out
        assert result.exit_code == sshexec.TIMEOUT_EXIT_CODE

    def test_it_says_the_handshake_succeeded_and_the_command_did_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction the operator needs: unreachable, or unresponsive?"""
        a_hanging_target(monkeypatch)
        result = sshexec.run(A_HOST.ssh_args, "true", timeout=7)

        assert "7s" in result.stderr
        assert "connection was accepted" in result.stderr
        assert "Nothing was confirmed either way" in result.stderr

    def test_an_ordinary_failure_is_not_marked_as_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="no such file"),
        )
        result = sshexec.run(A_HOST.ssh_args, "cat /nope")

        assert not result.ok and not result.timed_out

    @pytest.mark.parametrize(
        ("partial", "expected"),
        [(b"half a line", "half a line"), ("half a line", "half a line"), (None, "")],
    )
    def test_partial_output_survives_however_it_arrives(
        self, partial: object, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Often the most diagnostic part of a hang, and its type varies."""
        a_hanging_target(monkeypatch, partial=partial)
        assert sshexec.run(A_HOST.ssh_args, "true").stdout == expected


class TestTheCommandsThatHangAboveIt:
    """The whole point of #13: `pless preflight` must report, not crash."""

    @pytest.mark.parametrize(
        ("call", "error"),
        [
            (lambda: deploy.http_status(A_HOST), deploy.DeployError),
            (lambda: backup.snapshots(A_HOST), backup.BackupError),
            (lambda: storage.resolve_data_device(_cfg(), A_HOST), storage.StorageError),
        ],
    )
    def test_a_hang_becomes_the_module_s_own_error(
        self, call, error, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_hanging_target(monkeypatch)
        with pytest.raises(error):
            call()


class TestRegistryRefusals:
    """#19 — the cause was in the journal, and only if you went looking."""

    @pytest.mark.parametrize(
        "text",
        [
            "unauthorized: authentication required",
            "toomanyrequests: You have reached your pull rate limit",
            "denied: requested access to the resource is denied",
            "Too Many Requests",
            "error: rate limit exceeded",
        ],
    )
    def test_the_shapes_a_registry_says_no_in(self, text: str) -> None:
        assert deploy.registry_refused(text)

    @pytest.mark.parametrize(
        "text",
        [
            "no space left on device",
            "port is already allocated",
            "yaml: line 4: mapping values are not allowed",
            "",
        ],
    )
    def test_unrelated_failures_are_not_blamed_on_the_registry(self, text: str) -> None:
        assert not deploy.registry_refused(text)


class FakeRemote:
    """Answers remote commands by substring; records the order."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.commands: list[str] = []

    def run(self, destination, remote_command, timeout=60, input_text=None):
        self.commands.append(remote_command)
        for fragment, (code, text) in self.answers.items():
            if fragment in remote_command:
                return sshexec.SshResult(code, "" if code else text, text if code else "")
        return sshexec.SshResult(0, "", "")

    def index_of(self, fragment: str) -> int:
        for position, command in enumerate(self.commands):
            if fragment in command:
                return position
        raise AssertionError(f"No command contained {fragment!r}: {self.commands}")


class TestPullIsExplanatoryNotFatal:
    def test_a_clean_pull_reports_no_problem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sshexec, "run", FakeRemote().run)
        assert deploy.pull(A_HOST) == ""

    def test_a_refused_pull_is_described_rather_than_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`docker compose pull` contacts the registry even when images are
        local, so a rate limit must not break a redeploy that would have
        started from cache."""
        monkeypatch.setattr(
            sshexec,
            "run",
            FakeRemote({"docker compose pull": (1, "unauthorized: authentication required")}).run,
        )
        problem = deploy.pull(A_HOST)

        assert "pull limit" in problem
        assert "again often works" in problem

    def test_other_pull_failures_are_passed_through_plainly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sshexec, "run", FakeRemote({"docker compose pull": (1, "no space left")}).run
        )
        problem = deploy.pull(A_HOST)

        assert "no space left" in problem
        assert "pull limit" not in problem


class TestJournalMarkersAreNotContent:
    """A drill found `-- No entries --` being read as the journal's answer,
    which discarded the fallback and lost the registry explanation with it."""

    @pytest.mark.parametrize(
        "text",
        [
            "-- No entries --",
            "-- No entries --\n",
            "-- Boot 1a2b3c4d is the last boot --",
            "",
            "   ",
        ],
    )
    def test_markers_and_blanks_count_as_nothing(self, text: str) -> None:
        assert deploy.journal_content(text) == ""

    def test_real_lines_survive_with_the_markers_removed(self) -> None:
        text = (
            "-- Logs begin at Sat 2026-10-03 19:44:00 CEST --\n"
            "Oct 03 19:44:20 host sh[4237]: no space left on device\n"
            "-- No more entries --"
        )
        assert deploy.journal_content(text) == (
            "Oct 03 19:44:20 host sh[4237]: no space left on device"
        )


class TestStartUnitSaysWhatTheUnitSaid:
    SYSTEMD = "Job for paperless.service failed because the control process exited"

    def test_a_clean_start_says_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sshexec, "run", FakeRemote().run)
        deploy.start_unit(A_HOST, "paperless.service")

    def test_the_journal_is_fetched_and_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        remote = FakeRemote(
            {
                "systemctl start": (1, self.SYSTEMD),
                "journalctl": (0, "docker[6044]: no space left on device"),
            }
        )
        monkeypatch.setattr(sshexec, "run", remote.run)
        with pytest.raises(deploy.DeployError) as exc:
            deploy.start_unit(A_HOST, "paperless.service")

        assert "no space left on device" in str(exc.value)
        assert remote.index_of("systemctl start") < remote.index_of("journalctl")

    def test_the_journal_is_scoped_to_the_attempt_that_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A unit that has failed before otherwise mixes attempts together."""
        remote = FakeRemote(
            {
                "systemctl start": (1, self.SYSTEMD),
                "InvocationID": (0, "a1b2c3d4"),
                "_SYSTEMD_INVOCATION_ID=a1b2c3d4": (0, "this attempt only"),
                "journalctl -u": (0, "every attempt ever"),
            }
        )
        monkeypatch.setattr(sshexec, "run", remote.run)
        with pytest.raises(deploy.DeployError) as exc:
            deploy.start_unit(A_HOST, "paperless.service")

        assert "this attempt only" in str(exc.value)
        assert "every attempt ever" not in str(exc.value)

    def test_it_falls_back_to_the_line_count_without_an_invocation_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote = FakeRemote(
            {
                "systemctl start": (1, self.SYSTEMD),
                "InvocationID": (0, ""),
                "journalctl -u": (0, "the last twenty lines"),
            }
        )
        monkeypatch.setattr(sshexec, "run", remote.run)
        with pytest.raises(deploy.DeployError, match="the last twenty lines"):
            deploy.start_unit(A_HOST, "paperless.service")

    def test_a_registry_refusal_in_the_journal_is_named_as_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The drill's exact failure: this was reported only as systemd's summary."""
        monkeypatch.setattr(
            sshexec,
            "run",
            FakeRemote(
                {
                    "systemctl start": (1, self.SYSTEMD),
                    "journalctl": (0, "docker[6044]: unauthorized: authentication required"),
                }
            ).run,
        )
        with pytest.raises(deploy.DeployError) as exc:
            deploy.start_unit(A_HOST, "paperless.service")

        message = str(exc.value)
        assert "pull limit" in message
        assert "again often works" in message

    def test_a_pull_problem_becomes_relevant_when_the_start_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sshexec, "run", FakeRemote({"systemctl start": (1, self.SYSTEMD)}).run)
        with pytest.raises(deploy.DeployError) as exc:
            deploy.start_unit(A_HOST, "paperless.service", context="Fetching the images failed: x")

        assert "Fetching the images failed: x" in str(exc.value)

    def test_a_hanging_start_is_reported_as_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_hanging_target(monkeypatch)
        with pytest.raises(deploy.DeployError, match="timed out"):
            deploy.start_unit(A_HOST, "paperless.service")


class TestInstallFetchesBeforeItStarts:
    def test_the_images_are_pulled_as_their_own_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Otherwise the pull happens inside the unit, where only systemd sees it."""
        remote = FakeRemote()
        monkeypatch.setattr(sshexec, "run", remote.run)
        monkeypatch.setattr(
            storage,
            "status",
            lambda cfg, target: storage.StorageStatus(
                device="/dev/sdb", is_luks=True, is_open=True, is_mounted=True
            ),
        )
        secrets = config.Secrets(
            paperless_admin_password="x", paperless_secret_key="y", postgres_password="z"
        )
        deploy.install(_cfg(), secrets, A_HOST)

        assert remote.index_of("docker compose pull") < remote.index_of("systemctl start")


class TestTheFallbackSurvivesAnEmptyInvocationJournal:
    def test_a_no_entries_answer_does_not_discard_the_explanation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exactly what the drill hit: scoped journal empty, registry note lost."""
        remote = FakeRemote(
            {
                "systemctl start": (1, "Job for x failed because the control process exited"),
                "InvocationID": (0, "a1b2c3d4"),
                "_SYSTEMD_INVOCATION_ID=a1b2c3d4": (0, "-- No entries --"),
                "journalctl -u": (0, "sh[4250]: unauthorized: authentication required"),
            }
        )
        monkeypatch.setattr(sshexec, "run", remote.run)
        with pytest.raises(deploy.DeployError) as exc:
            deploy.start_unit(A_HOST, "paperless.service")

        message = str(exc.value)
        assert "pull limit" in message, "the registry explanation was lost"
        assert "-- No entries --" not in message
