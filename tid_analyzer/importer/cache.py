from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

import duckdb

from tid_analyzer.config import ImportFilters
if TYPE_CHECKING:
    from tid_analyzer.importer.parser import StationRow

OBS_COLUMNS = "station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat"
CACHE_VERSION = "duckdb_daily_v6"
PARSER_VERSION = "parser_v2"


def epoch_index_for_time(time_h: float, step_seconds: int = 30) -> int:
    return round(time_h * 3600 / step_seconds)


def elevation_key(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", "p")


def cache_path_for_day(cache_root: Path, year: int | None, doy: int | None, filters: ImportFilters | None = None) -> Path:
    name = f"{year}_{doy:03d}" if year is not None and doy is not None else "unknown_day"
    elev = f"elev_{elevation_key((filters or ImportFilters()).min_elevation_deg)}"
    return cache_root / name / elev / "tid_day.duckdb"


def row_to_record(row: "StationRow", filters: ImportFilters) -> tuple[object, ...]:
    # Some tests and older cache-building helpers used the pre-dataclass positional
    # order (station, prn, time_h, ...).  Normalize that legacy shape here while
    # keeping the canonical StationRow field order unchanged.
    station = row.station
    prn = row.prn
    time_h = row.time_h
    dtec = row.dtec
    if isinstance(time_h, str) and not isinstance(prn, str):
        prn, time_h, dtec = time_h, prn, row.dtec
    return (station, prn, time_h, epoch_index_for_time(float(time_h), filters.epoch_step_seconds), dtec, row.azimuth, row.elevation, row.ipp_lon, row.ipp_lat)


def configure_connection(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"PRAGMA threads={max(1, min(os.cpu_count() or 1, 8))}")


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            station VARCHAR, prn VARCHAR, time_h DOUBLE, epoch_index INTEGER, dtec DOUBLE,
            azimuth DOUBLE, elevation DOUBLE, ipp_lon DOUBLE, ipp_lat DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS imported_files (
            filename VARCHAR PRIMARY KEY,
            total_nonempty_rows BIGINT,
            parsed_rows BIGINT,
            malformed_rows BIGINT,
            non_gps_rows BIGINT,
            low_elevation_rows BIGINT,
            out_of_bounds_rows BIGINT,
            valid_row_count BIGINT,
            status VARCHAR,
            error_message VARCHAR,
            relative_filename VARCHAR,
            byte_size BIGINT,
            mtime_ns BIGINT,
            sha256 VARCHAR,
            normalized_observation_digest VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS stations (
            station VARCHAR PRIMARY KEY, full_site_id VARCHAR, city VARCHAR, country VARCHAR, domes VARCHAR,
            longitude DOUBLE, latitude DOUBLE, height DOUBLE, x DOUBLE, y DOUBLE, z DOUBLE, coordinate_source VARCHAR,
            reference_frame VARCHAR, coordinate_epoch VARCHAR, resolved BOOLEAN, resolution_note VARCHAR
        )
    """)


def create_metadata_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            source_folder VARCHAR, year INTEGER, doy INTEGER, min_elevation_deg DOUBLE,
            lon_min DOUBLE, lon_max DOUBLE, lat_min DOUBLE, lat_max DOUBLE,
            source_file_count INTEGER, total_rows_seen BIGINT, valid_rows_stored BIGINT,
            created_at VARCHAR, completed BOOLEAN, application_cache_version VARCHAR,
            parser_version VARCHAR, source_content_digest VARCHAR, imported_observation_digest VARCHAR,
            authoritative_fingerprint VARCHAR, import_filters_json VARCHAR, grid_resolution_deg DOUBLE
        )
    """)




def ensure_identity_columns(con: duckdb.DuckDBPyConnection) -> None:
    for table, columns in {
        "imported_files": {
            "relative_filename": "VARCHAR", "byte_size": "BIGINT", "mtime_ns": "BIGINT", "sha256": "VARCHAR", "normalized_observation_digest": "VARCHAR",
        },
        "metadata": {
            "parser_version": "VARCHAR", "source_content_digest": "VARCHAR", "imported_observation_digest": "VARCHAR",
            "authoritative_fingerprint": "VARCHAR", "import_filters_json": "VARCHAR", "grid_resolution_deg": "DOUBLE",
        },
    }.items():
        existing = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
        for name, typ in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def aggregate_digest(values: Iterable[str]) -> str:
    h = hashlib.sha256()
    for value in sorted(str(v) for v in values if v):
        h.update(value.encode("utf-8")); h.update(b"\n")
    return h.hexdigest()


def authoritative_fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

def csv_relation_sql(path: Path) -> str:
    safe_path = str(path).replace("'", "''")
    return (
        f"read_csv('{safe_path}', delim=';', header=false, "
        "columns={'time_raw':'VARCHAR','prn_raw':'VARCHAR','dtec_raw':'VARCHAR',"
        "'azimuth_raw':'VARCHAR','elevation_raw':'VARCHAR','ipp_lon_raw':'VARCHAR',"
        "'ipp_lat_raw':'VARCHAR','extra_raw':'VARCHAR'}, null_padding=true, auto_detect=false)"
    )


def parsed_relation_sql(path: Path) -> str:
    return f"""
        SELECT
            TRY_CAST(trim(time_raw) AS DOUBLE) AS time_h,
            upper(trim(prn_raw)) AS prn,
            TRY_CAST(trim(dtec_raw) AS DOUBLE) AS dtec,
            TRY_CAST(trim(azimuth_raw) AS DOUBLE) AS azimuth,
            TRY_CAST(trim(elevation_raw) AS DOUBLE) AS elevation,
            TRY_CAST(trim(ipp_lon_raw) AS DOUBLE) AS ipp_lon,
            TRY_CAST(trim(ipp_lat_raw) AS DOUBLE) AS ipp_lat,
            extra_raw
        FROM {csv_relation_sql(path)}
    """


def parsed_valid_expr() -> str:
    return "time_h IS NOT NULL AND prn IS NOT NULL AND dtec IS NOT NULL AND azimuth IS NOT NULL AND elevation IS NOT NULL AND ipp_lon IS NOT NULL AND ipp_lat IS NOT NULL AND (extra_raw IS NULL OR trim(extra_raw) = '')"


def normalized_prn_expr() -> str:
    return "prn"


def source_file_sql(path: Path, station: str, filters: ImportFilters) -> str:
    safe_station = station.replace("'", "''")
    prn = normalized_prn_expr()
    return f"""
        SELECT trim('{safe_station}')::VARCHAR AS station, {prn} AS prn, time_h,
               CAST(ROUND(time_h * 3600.0 / {filters.epoch_step_seconds}) AS INTEGER) AS epoch_index,
               dtec, azimuth, elevation, ipp_lon, ipp_lat
        FROM ({parsed_relation_sql(path)})
        WHERE {parsed_valid_expr()}
          AND starts_with({prn}, upper(trim('{filters.constellation_prefix}')))
          AND elevation >= {filters.min_elevation_deg}
          AND ipp_lon BETWEEN {filters.lon_min} AND {filters.lon_max}
          AND ipp_lat BETWEEN {filters.lat_min} AND {filters.lat_max}
    """


def finalize_cache(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DROP TABLE IF EXISTS epochs")
    con.execute("""
        CREATE TABLE epochs AS
        SELECT prn, epoch_index, AVG(time_h) AS time_h, COUNT(*) AS row_count, COUNT(DISTINCT station) AS station_count
        FROM observations GROUP BY prn, epoch_index
    """)
    con.execute("DROP VIEW IF EXISTS prn_epochs")
    con.execute("CREATE VIEW prn_epochs AS SELECT * FROM epochs")
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_observations_prn ON observations(prn)",
        "CREATE INDEX IF NOT EXISTS idx_observations_station ON observations(station)",
        "CREATE INDEX IF NOT EXISTS idx_observations_time_h ON observations(time_h)",
        "CREATE INDEX IF NOT EXISTS idx_observations_prn_time ON observations(prn, time_h)",
        "CREATE INDEX IF NOT EXISTS idx_observations_station_prn ON observations(station, prn)",
        "CREATE INDEX IF NOT EXISTS idx_epochs_prn_epoch ON epochs(prn, epoch_index)",
    ]:
        try:
            con.execute(stmt)
        except duckdb.Error:
            pass


def cache_is_valid(cache_path: Path, folder: Path, files: list[Path], year: int | None, doy: int | None, filters: ImportFilters) -> bool:
    if not cache_path.exists():
        return False
    try:
        with duckdb.connect(str(cache_path)) as con:
            ensure_identity_columns(con)
            row = con.execute("SELECT year, doy, min_elevation_deg, lon_min, lon_max, lat_min, lat_max, source_file_count, completed, application_cache_version, valid_rows_stored, source_content_digest, imported_observation_digest, parser_version, import_filters_json FROM metadata LIMIT 1").fetchone()
            if row is None or not row[11] or not row[12]:
                return False
            file_rows = con.execute("SELECT relative_filename, byte_size, mtime_ns FROM imported_files ORDER BY relative_filename").fetchall()
            current = []
            for path in files:
                st = path.stat()
                current.append((path.name, int(st.st_size), int(st.st_mtime_ns)))
            return (
                row[0] == year and row[1] == doy
                and float(row[2]) == float(filters.min_elevation_deg)
                and float(row[3]) == filters.lon_min and float(row[4]) == filters.lon_max
                and float(row[5]) == filters.lat_min and float(row[6]) == filters.lat_max
                and int(row[7]) == len(files) and bool(row[8]) and str(row[9]) == CACHE_VERSION and int(row[10] or 0) > 0
                and str(row[13]) == PARSER_VERSION and str(row[14] or "") == canonical_json(filters.as_manifest_dict())
                and sorted(file_rows) == sorted(current)
            )
    except duckdb.Error:
        return False

def create_daily_cache(cache_path: Path, rows: Iterable["StationRow"], metadata: dict[str, object], filters: ImportFilters) -> None:
    # Reference/fallback implementation retained for tests and malformed files.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        cache_path.unlink()
    with duckdb.connect(str(cache_path)) as con:
        configure_connection(con); create_schema(con); create_metadata_table(con); ensure_identity_columns(con)
        con.executemany(f"INSERT INTO observations ({OBS_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [row_to_record(r, filters) for r in rows])
        finalize_cache(con)
        con.execute("INSERT INTO metadata (source_folder, year, doy, min_elevation_deg, lon_min, lon_max, lat_min, lat_max, source_file_count, total_rows_seen, valid_rows_stored, created_at, completed, application_cache_version, parser_version, import_filters_json, grid_resolution_deg) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [metadata.get("source_folder"), metadata.get("year"), metadata.get("doy"), filters.min_elevation_deg, filters.lon_min, filters.lon_max, filters.lat_min, filters.lat_max, metadata.get("source_file_count", 0), metadata.get("total_rows_seen", 0), metadata.get("valid_rows", 0), datetime.now(timezone.utc).isoformat(), True, CACHE_VERSION, PARSER_VERSION, canonical_json(filters.as_manifest_dict()), metadata.get("grid_resolution_deg", 1.0)])


def connect_cache(cache_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(cache_path))
