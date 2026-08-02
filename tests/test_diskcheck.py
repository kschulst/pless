import pytest

from pless.diskcheck import GB, Recommendation, parse_df_output, project

DF_OUTPUT = """\
Filesystem     1024-blocks    Used Available Capacity Mounted on
/dev/sda1         39519836 8123456  29379612      22% /
"""


def test_parse_df_output() -> None:
    snapshot = parse_df_output(DF_OUTPUT)
    assert snapshot.total_kb == 39519836
    assert snapshot.used_kb == 8123456
    assert snapshot.avail_kb == 29379612
    assert 20 < snapshot.used_percent < 21


def test_parse_df_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_df_output("df: /: No such file or directory")


def test_small_upload_on_healthy_disk_proceeds() -> None:
    disk = parse_df_output(DF_OUTPUT)  # ~40 GB disk, ~8 GB brukt
    projection = project(disk, upload_bytes=3 * GB, min_free_gb=10, max_used_percent=70)
    # 3 GB * 3.4 ≈ 10 GB vekst → ~18 GB brukt av 40 → ok
    assert projection.recommendation == Recommendation.PROCEED


def test_large_upload_wants_more_storage() -> None:
    disk = parse_df_output(DF_OUTPUT)
    projection = project(disk, upload_bytes=15 * GB, min_free_gb=10, max_used_percent=70)
    # 15 GB * 3.4 ≈ 51 GB vekst på en 40 GB-disk → håpløst uansett batching
    assert projection.recommendation == Recommendation.ADD_STORAGE


def test_borderline_upload_suggests_smaller_batches() -> None:
    disk = parse_df_output(DF_OUTPUT)
    projection = project(disk, upload_bytes=6 * GB, min_free_gb=10, max_used_percent=70)
    # ~20 GB vekst → over terskler, men innenfor disken hvis eksport ryddes/batches
    assert projection.recommendation == Recommendation.REDUCE_BATCH


def test_export_copy_can_be_excluded() -> None:
    disk = parse_df_output(DF_OUTPUT)
    with_export = project(disk, upload_bytes=5 * GB, min_free_gb=10, max_used_percent=70)
    without_export = project(
        disk, upload_bytes=5 * GB, min_free_gb=10, max_used_percent=70, include_export_copy=False
    )
    assert without_export.projected_growth_bytes < with_export.projected_growth_bytes
