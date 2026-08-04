"""Disk estimator: project usage on the target after import and advise.

Pure logic with no I/O, so it is testable without a machine. The df output
is collected by sshexec.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Deliberately pessimistic growth factors:
# - Paperless keeps the original AND writes an OCR'd PDF/A archive copy,
#   plus thumbnails, Postgres rows and the search index. 2.2x covers it.
# - document_exporter writes another full copy onto the same disk.
INGEST_GROWTH_FACTOR = 2.2
EXPORT_COPY_FACTOR = 1.2

GB = 1024**3


class Recommendation(StrEnum):
    PROCEED = "proceed"
    REDUCE_BATCH = "reduce-batch"
    ADD_STORAGE = "add-storage"


@dataclass
class DiskSnapshot:
    """From `df -Pk <path>` on the target. All values in kibibytes."""

    total_kb: int
    used_kb: int
    avail_kb: int

    @property
    def total_bytes(self) -> int:
        return self.total_kb * 1024

    @property
    def used_bytes(self) -> int:
        return self.used_kb * 1024

    @property
    def avail_bytes(self) -> int:
        return self.avail_kb * 1024

    @property
    def used_percent(self) -> float:
        return 100.0 * self.used_kb / self.total_kb if self.total_kb else 0.0


def parse_df_output(output: str) -> DiskSnapshot:
    """Parse POSIX `df -Pk <path>` output: a header plus one data line."""
    lines = [line for line in output.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"Unexpected df output: {output!r}")
    fields = lines[-1].split()
    # Filesystem 1024-blocks Used Available Capacity Mounted-on
    return DiskSnapshot(total_kb=int(fields[1]), used_kb=int(fields[2]), avail_kb=int(fields[3]))


@dataclass
class Projection:
    disk: DiskSnapshot
    upload_bytes: int
    projected_growth_bytes: int
    include_export_copy: bool
    min_free_gb: int
    max_used_percent: int

    @property
    def projected_used_bytes(self) -> int:
        return self.disk.used_bytes + self.projected_growth_bytes

    @property
    def projected_used_percent(self) -> float:
        return 100.0 * self.projected_used_bytes / self.disk.total_bytes

    @property
    def projected_free_gb(self) -> float:
        return (self.disk.total_bytes - self.projected_used_bytes) / GB

    @property
    def recommendation(self) -> Recommendation:
        if (
            self.projected_free_gb >= self.min_free_gb
            and self.projected_used_percent <= self.max_used_percent
        ):
            return Recommendation.PROCEED
        # If even an empty disk could not hold the growth within the limits,
        # batching will not help — the disk is simply too small.
        free_limit_bytes = self.min_free_gb * GB
        pct_limit_bytes = self.disk.total_bytes * self.max_used_percent / 100
        if self.projected_growth_bytes > min(
            pct_limit_bytes, self.disk.total_bytes - free_limit_bytes
        ):
            return Recommendation.ADD_STORAGE
        return Recommendation.REDUCE_BATCH


def project(
    disk: DiskSnapshot,
    upload_bytes: int,
    min_free_gb: int,
    max_used_percent: int,
    include_export_copy: bool = True,
) -> Projection:
    growth = upload_bytes * INGEST_GROWTH_FACTOR
    if include_export_copy:
        growth += upload_bytes * EXPORT_COPY_FACTOR
    return Projection(
        disk=disk,
        upload_bytes=upload_bytes,
        projected_growth_bytes=int(growth),
        include_export_copy=include_export_copy,
        min_free_gb=min_free_gb,
        max_used_percent=max_used_percent,
    )
