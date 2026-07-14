from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

zarr = pytest.importorskip("zarr")
pytest.importorskip("numcodecs")

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import CACHE_VERSION, cache_path_for_day, create_daily_cache
from tid_analyzer.importer.parser import StationRow
from tid_analyzer.interpolation.natural_neighbor import NaturalNeighborResult
from tid_analyzer.interpolation.storage import (
    ArcDescriptor,
    EpochGridLRU,
    create_or_open_interpolation_cache,
    create_source_fingerprint,
    has_epoch_result,
    invalidate_interpolation_cache,
    list_missing_epochs,
    read_epoch_result,
    validate_interpolation_cache,
    write_epoch_result,
)


def _daily_cache(tmp_path: Path) -> Path:
    cache = cache_path_for_day(tmp_path / "cache", 2024, 246, ImportFilters(min_elevation_deg=50))
    rows = [
        StationRow("AAA", "G24", 1.0, 1.0, 0.0, 55.0, 10.0, 45.0),
        StationRow("BBB", "G24", 1.0, 2.0, 0.0, 56.0, 11.0, 46.0),
        StationRow("CCC", "G24", 1.0, 3.0, 0.0, 57.0, 12.0, 47.0),
    ]
    create_daily_cache(cache, rows, {"source_folder": "src", "year": 2024, "doy": 246, "source_file_count": 2, "valid_rows": 3}, ImportFilters(min_elevation_deg=50))
    return cache


def _result(epoch: int = 0, status: str = "ready") -> NaturalNeighborResult:
    lon = np.array([-20.0, -19.5], dtype=np.float64)
    lat = np.array([20.0, 20.5], dtype=np.float64)
    values = np.array([[1.25, np.nan], [2.5, 3.5]], dtype=np.float64)
    mask = np.isfinite(values)
    return NaturalNeighborResult("G24", epoch, epoch / 120.0, "method", "EPSG:3035", 0.5, lon, lat, values, mask, 3, 3, status, "ok")


def _cache(tmp_path: Path, grid_step: float = 0.5) -> Path:
    daily = _daily_cache(tmp_path)
    return create_or_open_interpolation_cache(
        cache_root=tmp_path / "cache",
        daily_cache_path=daily,
        year=2024,
        doy=246,
        minimum_elevation_deg=50,
        arcs=[ArcDescriptor("G24", 1, 3)],
        grid_step_deg=grid_step,
    )


def test_cache_folder_naming_by_year_doy_elevation(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    assert cache_dir == tmp_path / "cache" / "2024_246" / "elev_50" / "interpolation"


def test_metadata_is_created_correctly(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    metadata = json.loads((cache_dir / "metadata.json").read_text())
    assert metadata["cache_format_version"] == "interpolation_zarr_v1"
    assert metadata["source_cache_version"] == CACHE_VERSION
    assert metadata["year"] == 2024
    assert metadata["doy"] == 246
    assert metadata["minimum_elevation_deg"] == 50.0
    assert metadata["arc_entries"][0]["store_path"] == "G24_arc_1.zarr"


def test_one_epoch_can_be_written_and_read(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(), arc_index=1)
    payload = read_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=0)
    assert payload["status"] == "ready"
    assert payload["values"].shape == (2, 2)
    assert np.isclose(payload["values"][0, 0], 1.25)


def test_reading_one_epoch_does_not_require_all_epochs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(0), arc_index=1)
    write_epoch_result(cache_dir, _result(2), arc_index=1)
    group = zarr.open_group(str(cache_dir / "G24_arc_1.zarr"), mode="r")
    original = group["dtec_grid"].__getitem__
    keys = []

    def spy(key):
        keys.append(key)
        return original(key)

    monkeypatch.setattr(group["dtec_grid"], "__getitem__", spy)
    payload = read_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=2)
    assert payload["epoch_index"] == 2
    # The storage function indexes one epoch with [idx, :, :] rather than [:].
    assert payload["values"].shape == (2, 2)


def test_float32_and_boolean_storage_types(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(), arc_index=1)
    group = zarr.open_group(str(cache_dir / "G24_arc_1.zarr"), mode="r")
    assert group["dtec_grid"].dtype == np.dtype("float32")
    assert group["valid_mask"].dtype == np.dtype("bool")


def test_incompatible_grid_step_invalidates_compatibility(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path, grid_step=0.5)
    result = validate_interpolation_cache(cache_dir, grid_step_deg=1.0)
    assert not result.compatible
    assert result.stale


def test_incompatible_source_cache_version_invalidates_compatibility(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    fingerprint = create_source_fingerprint(daily_cache_path=_daily_cache(tmp_path), year=2024, doy=246, minimum_elevation_deg=50)
    fingerprint["source_cache_version"] = "other"
    result = validate_interpolation_cache(cache_dir, expected_fingerprint=fingerprint)
    assert not result.compatible
    assert "fingerprint" in result.reason


def test_pending_epoch_is_missing_and_ready_epoch_is_not(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(1), arc_index=1)
    missing = list_missing_epochs(cache_dir, prn="G24", arc_index=1, expected_epoch_count=3)
    assert missing == [0, 2]
    assert has_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=1)


def test_successful_epochs_survive_process_restart(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(), arc_index=1)
    del cache_dir
    reopened = tmp_path / "cache" / "2024_246" / "elev_50" / "interpolation"
    assert read_epoch_result(reopened, prn="G24", arc_index=1, epoch_index=0)["status"] == "ready"


def test_lru_never_exceeds_configured_capacity(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    for epoch in range(4):
        write_epoch_result(cache_dir, _result(epoch), arc_index=1)
    lru = EpochGridLRU(max_size=2)
    for epoch in range(4):
        read_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=epoch, lru=lru)
        assert len(lru) <= 2


def test_stale_cache_is_not_silently_reused(tmp_path: Path) -> None:
    cache_dir = _cache(tmp_path)
    write_epoch_result(cache_dir, _result(), arc_index=1)
    invalidate_interpolation_cache(cache_dir, "test mismatch")
    with pytest.raises(RuntimeError, match="test mismatch"):
        read_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=0)
