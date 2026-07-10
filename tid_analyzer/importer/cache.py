from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import duckdb

from tid_analyzer.config import ImportFilters
if TYPE_CHECKING:
    from tid_analyzer.importer.parser import StationRow

OBS_COLUMNS = "station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat"


def epoch_index_for_time(time_h: float, step_seconds: int = 30) -> int:
    return round(time_h * 3600 / step_seconds)


def cache_path_for_day(cache_root: Path, year: int | None, doy: int | None) -> Path:
    name = f"{year}_{doy:03d}" if year is not None and doy is not None else "unknown_day"
    return cache_root / name / "tid_day.duckdb"


def row_to_record(row: "StationRow", filters: ImportFilters) -> tuple[object, ...]:
    return (row.station, row.prn, row.time_h, epoch_index_for_time(row.time_h, filters.epoch_step_seconds), row.dtec, row.azimuth, row.elevation, row.ipp_lon, row.ipp_lat)


def create_daily_cache(cache_path: Path, rows: Iterable["StationRow"], metadata: dict[str, object], filters: ImportFilters) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        cache_path.unlink()
    with duckdb.connect(str(cache_path)) as con:
        con.execute("""
            CREATE TABLE observations (
                station TEXT,
                prn TEXT,
                time_h DOUBLE,
                epoch_index INTEGER,
                dtec DOUBLE,
                azimuth DOUBLE,
                elevation DOUBLE,
                ipp_lon DOUBLE,
                ipp_lat DOUBLE
            )
        """)
        batch: list[tuple[object, ...]] = []
        for row in rows:
            batch.append(row_to_record(row, filters))
            if len(batch) >= 10000:
                con.executemany(f"INSERT INTO observations ({OBS_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                batch.clear()
        if batch:
            con.executemany(f"INSERT INTO observations ({OBS_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
        con.execute("""
            CREATE TABLE prn_epochs AS
            SELECT prn, epoch_index, AVG(time_h) AS time_h, COUNT(*) AS row_count, COUNT(DISTINCT station) AS station_count
            FROM observations
            GROUP BY prn, epoch_index
        """)
        enriched = dict(metadata)
        enriched["created_at"] = datetime.now(timezone.utc).isoformat()
        enriched["filters"] = json.dumps(filters.as_manifest_dict(), sort_keys=True)
        con.execute("""
            CREATE TABLE metadata (
                source_folder TEXT, year INTEGER, doy INTEGER, min_time_h DOUBLE, max_time_h DOUBLE,
                station_count INTEGER, prn_count INTEGER, valid_rows INTEGER, created_at TEXT, filters TEXT
            )
        """)
        con.execute(
            "INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [enriched.get("source_folder"), enriched.get("year"), enriched.get("doy"), enriched.get("min_time_h"), enriched.get("max_time_h"), enriched.get("station_count"), enriched.get("prn_count"), enriched.get("valid_rows"), enriched.get("created_at"), enriched.get("filters")],
        )
        for stmt in [
            "CREATE INDEX idx_observations_prn ON observations(prn)",
            "CREATE INDEX idx_observations_station ON observations(station)",
            "CREATE INDEX idx_observations_time_h ON observations(time_h)",
            "CREATE INDEX idx_observations_prn_time ON observations(prn, time_h)",
            "CREATE INDEX idx_observations_station_prn ON observations(station, prn)",
            "CREATE INDEX idx_prn_epochs_prn_epoch ON prn_epochs(prn, epoch_index)",
        ]:
            try:
                con.execute(stmt)
            except duckdb.Error:
                pass


def connect_cache(cache_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(cache_path), read_only=True)
