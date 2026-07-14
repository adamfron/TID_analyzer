from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("zarr")
pytest.importorskip("numcodecs")

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import create_daily_cache
from tid_analyzer.importer.parser import StationRow
from tid_analyzer.interpolation.natural_neighbor import NaturalNeighborResult
from tid_analyzer.interpolation.orchestrator import build_interpolation_plan, eligible_arcs_from_daily, _max_workers
from tid_analyzer.interpolation.storage import create_or_open_interpolation_cache, write_epoch_result, read_epoch_result, ArcDescriptor


def _rows(prn: str = "G24", epochs=(0.0, 2.0), stations: int = 100) -> list[StationRow]:
    return [StationRow(f"S{i:03d}", prn, t, float(i), 0.0, 70.0, 10.0 + i * 0.01, 45.0 + i * 0.01) for t in epochs for i in range(stations)]


def _daily(tmp_path: Path, rows: list[StationRow]) -> Path:
    path = tmp_path / "cache" / "2024_246" / "elev_50" / "tid_day.duckdb"
    create_daily_cache(path, rows, {"source_folder": "src", "year": 2024, "doy": 246, "source_file_count": 1, "valid_rows": len(rows)}, ImportFilters(min_elevation_deg=50))
    return path


def _result(epoch: int, status: str = "ready") -> NaturalNeighborResult:
    lon = np.array([-20.0, -19.5]); lat = np.array([20.0, 20.5]); values = np.ones((2, 2))
    return NaturalNeighborResult("G24", epoch, epoch / 120, "method", "EPSG:3035", 0.5, lon, lat, values, np.isfinite(values), 3, 3, status, "ok")


def test_only_eligible_arcs_produce_deduplicated_jobs(tmp_path: Path) -> None:
    daily = _daily(tmp_path, _rows(stations=100) + _rows("G25", stations=99))
    arcs = eligible_arcs_from_daily(daily)
    assert sum(1 for arc in arcs if arc["eligible_for_interpolation"]) == 1
    plan = build_interpolation_plan(cache_root=tmp_path / "cache", daily_cache_path=daily)
    assert plan["eligible_arc_count"] == 1
    assert [(j.prn, j.arc_index, j.epoch_index) for j in plan["jobs"]] == [("G24", 1, 0), ("G24", 1, 240)]


def test_already_ready_epochs_are_skipped_and_resume_preserves_maps(tmp_path: Path) -> None:
    daily = _daily(tmp_path, _rows(stations=100))
    cache_dir = create_or_open_interpolation_cache(cache_root=tmp_path / "cache", daily_cache_path=daily, year=2024, doy=246, minimum_elevation_deg=50, arcs=[ArcDescriptor("G24", 1, 2)])
    write_epoch_result(cache_dir, _result(0), arc_index=1)
    plan = build_interpolation_plan(cache_root=tmp_path / "cache", daily_cache_path=daily)
    assert plan["already_ready_count"] == 1
    assert [(j.prn, j.epoch_index) for j in plan["jobs"]] == [("G24", 240)]
    assert read_epoch_result(cache_dir, prn="G24", arc_index=1, epoch_index=0)["status"] == "ready"


def test_insufficient_points_is_not_retried_without_retry_failed(tmp_path: Path) -> None:
    daily = _daily(tmp_path, _rows(stations=100))
    cache_dir = create_or_open_interpolation_cache(cache_root=tmp_path / "cache", daily_cache_path=daily, year=2024, doy=246, minimum_elevation_deg=50, arcs=[ArcDescriptor("G24", 1, 2)])
    write_epoch_result(cache_dir, _result(0, "insufficient_points"), arc_index=1)
    assert [j.epoch_index for j in build_interpolation_plan(cache_root=tmp_path / "cache", daily_cache_path=daily)["jobs"]] == [240]
    assert [j.epoch_index for j in build_interpolation_plan(cache_root=tmp_path / "cache", daily_cache_path=daily, retry_failed=True)["jobs"]] == [0, 240]


def test_worker_default_is_conservative(monkeypatch) -> None:
    monkeypatch.delenv("TID_ANALYZER_INTERPOLATION_WORKERS", raising=False)
    assert 1 <= _max_workers() <= 4
