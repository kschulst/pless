from pathlib import Path

from pless import docscan


def make_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_classifies_supported_conversion_and_unsupported(tmp_path: Path) -> None:
    make_file(tmp_path / "faktura.pdf", b"%PDF")
    make_file(tmp_path / "kvittering.jpg")
    make_file(tmp_path / "notater.enex", b"<en-export/>")
    make_file(tmp_path / "backup.zip")

    result = docscan.scan(tmp_path)

    assert [f.path.name for f in result.supported] == ["faktura.pdf", "kvittering.jpg"]
    assert [f.path.name for f in result.needs_conversion] == ["notater.enex"]
    assert [f.path.name for f in result.unsupported] == ["backup.zip"]
    assert result.total_files == 4


def test_skips_hidden_and_system_files(tmp_path: Path) -> None:
    make_file(tmp_path / ".DS_Store")
    make_file(tmp_path / ".skjult.pdf")
    make_file(tmp_path / ".skjult_mappe" / "inni.pdf")
    make_file(tmp_path / "synlig" / "ok.pdf")

    result = docscan.scan(tmp_path)

    assert [f.path.name for f in result.supported] == ["ok.pdf"]
    assert result.total_files == 1


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    make_file(tmp_path / "SKANN.PDF")
    result = docscan.scan(tmp_path)
    assert len(result.supported) == 1


def test_duplicate_detection_via_hashes(tmp_path: Path) -> None:
    make_file(tmp_path / "a.pdf", b"samme innhold")
    make_file(tmp_path / "b" / "kopi.pdf", b"samme innhold")
    make_file(tmp_path / "c.pdf", b"unikt")

    result = docscan.scan(tmp_path, with_hashes=True)
    dupes = result.duplicate_groups()

    assert len(dupes) == 1
    (group,) = dupes.values()
    assert {f.path.name for f in group} == {"a.pdf", "kopi.pdf"}


def test_supported_bytes_sums_sizes(tmp_path: Path) -> None:
    make_file(tmp_path / "a.pdf", b"12345")
    make_file(tmp_path / "b.pdf", b"123")
    result = docscan.scan(tmp_path)
    assert result.supported_bytes == 8
