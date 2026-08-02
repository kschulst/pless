"""Rekursiv skanning og klassifisering av lokale dokumenter før import."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Konsumeres direkte av Paperless-ngx (med Tika/Gotenberg aktivert for Office-formatene).
SUPPORTED_EXTS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tiff",
    ".tif",
    ".gif",
    ".webp",
    ".txt",
    ".md",
    ".docx",
    ".doc",
    ".odt",
    ".rtf",
    ".xlsx",
    ".ods",
    ".pptx",
    ".odp",
    ".eml",
}

# Krever konvertering før Paperless kan ta dem (typisk Evernote-eksport).
NEEDS_CONVERSION_EXTS = {".enex", ".html", ".htm"}

SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}


@dataclass
class FileEntry:
    path: Path
    size: int
    sha256: str | None = None


@dataclass
class ScanResult:
    root: Path
    supported: list[FileEntry] = field(default_factory=list)
    needs_conversion: list[FileEntry] = field(default_factory=list)
    unsupported: list[FileEntry] = field(default_factory=list)
    by_extension: Counter = field(default_factory=Counter)

    @property
    def supported_bytes(self) -> int:
        return sum(f.size for f in self.supported)

    @property
    def needs_conversion_bytes(self) -> int:
        return sum(f.size for f in self.needs_conversion)

    @property
    def total_files(self) -> int:
        return len(self.supported) + len(self.needs_conversion) + len(self.unsupported)

    def duplicate_groups(self) -> dict[str, list[FileEntry]]:
        """Grupper av supported-filer med identisk sha256 (krever with_hashes)."""
        by_hash: dict[str, list[FileEntry]] = {}
        for entry in self.supported:
            if entry.sha256:
                by_hash.setdefault(entry.sha256, []).append(entry)
        return {h: entries for h, entries in by_hash.items() if len(entries) > 1}


def _is_hidden_or_system(path: Path) -> bool:
    return path.name.startswith(".") or path.name in SKIP_NAMES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: Path, with_hashes: bool = False) -> ScanResult:
    """Skann root rekursivt. Skjulte filer/mapper og systemfiler hoppes over.

    Sletter ingenting og følger ikke symlinker ut av treet.
    """
    result = ScanResult(root=root)
    if not root.is_dir():
        raise NotADirectoryError(f"Ikke en mappe: {root}")

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in relative_parts) or _is_hidden_or_system(path):
            continue

        ext = path.suffix.lower()
        entry = FileEntry(path=path, size=path.stat().st_size)
        result.by_extension[ext or "(uten endelse)"] += 1

        if ext in SUPPORTED_EXTS:
            if with_hashes:
                entry.sha256 = _sha256(path)
            result.supported.append(entry)
        elif ext in NEEDS_CONVERSION_EXTS:
            result.needs_conversion.append(entry)
        else:
            result.unsupported.append(entry)

    return result
