"""`docs upload`, driven through the CLI.

Every module here is covered by tests over pure functions, which is exactly why
`cli.py` is where drift hides: `preflight` once died with `AttributeError`
before checking anything, because nothing exercised the wiring. These tests run
the real `config`, `docscan` and `paperless` code and replace only the SSH round
trip, the HTTP transport and the config file.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from pless import cli, config, docscan, paperless, sshexec, tailscale

runner = CliRunner()


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    root = tmp_path / "documents"
    root.mkdir()
    (root / "invoice.pdf").write_bytes(b"one")
    (root / "receipt.pdf").write_bytes(b"two")
    return root


def _answers_tailscale(
    destination: list[str],
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
) -> sshexec.SshResult:
    """A target with Tailscale up, reporting the name it calls itself."""
    return sshexec.SshResult(
        exit_code=0,
        stdout='{"BackendState":"Running","Self":{"DNSName":"archive-01.tail.ts.net.",'
        '"TailscaleIPs":["100.64.0.1"]}}',
        stderr="",
    )


def _tailscale_down(
    destination: list[str],
    remote_command: str,
    timeout: int = 60,
    input_text: str | None = None,
) -> sshexec.SshResult:
    return sshexec.SshResult(exit_code=0, stdout="", stderr="")


class Accepting:
    """A Paperless that accepts everything and consumes it immediately."""

    def __init__(self) -> None:
        self.uploads = 0

    def __call__(
        self,
        method: str,
        url: str,
        token: str,
        files: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, object]:
        if method == "POST":
            self.uploads += 1
            return 200, f"task-{self.uploads}"
        return 200, {
            "results": [
                {"task_id": f"task-{n}", "status": "SUCCESS", "result": "ok"}
                for n in range(1, self.uploads + 1)
            ],
            "next": None,
        }


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., Accepting]:
    """The common arrangement: a configured host, Tailscale up, a token set, and
    a manifest in a temporary directory."""

    def apply(
        documents: Path,
        ssh: Callable[..., sshexec.SshResult] = _answers_tailscale,
        token: str = "api-token-value",
    ) -> Accepting:
        def load_config(path: Path | None = None) -> config.Config:
            return config.Config(
                host=config.HostConfig(address="nowhere.invalid", user="deploy"),
                paths=config.PathsConfig(local_documents=str(documents)),
            )

        transport = Accepting()
        monkeypatch.setattr(config, "load_config", load_config)
        monkeypatch.setattr(config, "find_config_file", lambda: tmp_path / "pless.toml")
        monkeypatch.setattr(
            config, "load_secrets", lambda: config.Secrets(paperless_api_token=token)
        )
        monkeypatch.setattr(sshexec, "run", ssh)
        monkeypatch.setattr(paperless, "http_transport", lambda timeout=120: transport)
        return transport

    return apply


def _upload(*args: str) -> Result:
    result = runner.invoke(cli.app, ["docs", "upload", "--wait", "0", *args])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result


def _flat(result: Result) -> str:
    """rich wraps to terminal width, so any assertion on a phrase is really an
    assertion about where the line broke unless the wrapping is collapsed."""
    return " ".join(result.output.split())


class TestTheHappyPath:
    def test_it_uploads_and_reports_the_archive(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        transport = wired(collection)
        result = _upload()
        assert result.exit_code == 0
        assert transport.uploads == 2
        assert "2 document(s) in the archive" in _flat(result)

    def test_it_defaults_to_the_configured_directory(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        transport = wired(collection)
        _upload()  # no path argument
        assert transport.uploads == 2

    def test_a_dry_run_uploads_nothing(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        transport = wired(collection)
        result = _upload("--dry-run")
        assert transport.uploads == 0
        assert "nothing was uploaded" in _flat(result)

    def test_running_again_uploads_nothing(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        wired(collection)
        _upload()
        second = wired(collection)
        result = _upload()
        assert second.uploads == 0
        assert "Nothing to do" in _flat(result)


class TestWhatItRefuses:
    def test_no_token_says_where_to_make_one(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        wired(collection, token="")
        result = _upload()
        assert result.exit_code != 0
        assert "PAPERLESS_API_TOKEN" in _flat(result)

    def test_tailscale_down_names_the_command(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        """The precondition an operator actually hits, and the message has to
        say what to run rather than that something is wrong."""
        wired(collection, ssh=_tailscale_down)
        result = _upload()
        assert result.exit_code != 0
        assert "pless tailscale up" in _flat(result)

    def test_a_corrupt_manifest_stops_the_command(
        self, collection: Path, wired: Callable[..., Accepting], tmp_path: Path
    ) -> None:
        wired(collection)
        (tmp_path / paperless.MANIFEST_NAME).write_text("{not json")
        result = _upload()
        assert result.exit_code != 0
        assert "re-upload" in _flat(result)

    def test_a_refusal_arrives_as_a_message_not_a_traceback(
        self, collection: Path, wired: Callable[..., Accepting], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wired(collection)

        def refuse(method, url, token, files=None, params=None):
            return 401, {"detail": "Invalid token."}

        monkeypatch.setattr(paperless, "http_transport", lambda timeout=120: refuse)
        result = _upload()
        assert result.exit_code != 0
        assert "Invalid token." in _flat(result)
        assert "Traceback" not in result.output

    def test_a_missing_directory_is_rejected_by_click(
        self, wired: Callable[..., Accepting]
    ) -> None:
        result = runner.invoke(cli.app, ["docs", "upload", "/nonexistent/path"])
        assert result.exit_code == 2  # click's own validation, before any work


class TestWhatItReportsSeparately:
    def test_uploaded_is_not_conflated_with_consumed(
        self, collection: Path, wired: Callable[..., Accepting], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction the whole feature exists to preserve. Two files
        uploaded, nothing consumed — and the operator must be told to come
        back rather than told it is done."""
        wired(collection)

        class StillWorking(Accepting):
            def __call__(self, method, url, token, files=None, params=None):
                if method == "POST":
                    self.uploads += 1
                    return 200, f"task-{self.uploads}"
                return 200, {"results": [], "next": None}

        transport = StillWorking()
        monkeypatch.setattr(paperless, "http_transport", lambda timeout=120: transport)
        result = _upload()
        assert transport.uploads == 2
        assert "0 document(s) in the archive" in _flat(result)
        assert "still being worked on" in _flat(result)
        assert "Run this again" in _flat(result)

    def test_unsupported_files_are_counted_not_dropped(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        (collection / "archive.zip").write_bytes(b"not a document")
        wired(collection)
        result = _upload("--dry-run")
        assert "1 unsupported" in _flat(result)

    def test_json_output_is_machine_readable(
        self, collection: Path, wired: Callable[..., Accepting]
    ) -> None:
        wired(collection)
        result = _upload("--json")
        assert '"in_archive": 2' in result.output


def _parameters(func: Callable[..., object]) -> list[tuple[str, object]]:
    return [(p.name, p.default) for p in inspect.signature(func).parameters.values()]


class TestTheFakes:
    @pytest.mark.parametrize("fake", [_answers_tailscale, _tailscale_down])
    def test_ssh_fakes_match_sshexec_run(self, fake: Callable[..., sshexec.SshResult]) -> None:
        assert _parameters(fake) == _parameters(sshexec.run)

    def test_the_transport_matches_the_protocol(self) -> None:
        assert [name for name, _ in _parameters(Accepting.__call__)][1:] == [
            p.name for p in inspect.signature(paperless.Transport.__call__).parameters.values()
        ][1:]

    def test_the_tailscale_fake_parses_as_the_real_thing(self) -> None:
        """If this payload stops parsing, the happy-path tests are testing a
        reachability failure and would still pass for the wrong reason."""
        status = tailscale.parse_status(_answers_tailscale([], "").stdout)
        assert status.hostname.startswith("archive-01")

    def test_the_real_scan_classifies_the_fixture(self, collection: Path) -> None:
        assert len(docscan.scan(collection, with_hashes=True).supported) == 2
