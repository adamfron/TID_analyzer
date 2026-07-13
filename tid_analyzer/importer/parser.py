from __future__ import annotations

import json
import re

import duckdb
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import CACHE_VERSION, cache_is_valid, cache_path_for_day, configure_connection, create_metadata_table, create_schema, finalize_cache, source_file_sql, row_to_record

ProgressCallback = Callable[[str, int, int, str], None]
CancelCallback = Callable[[], bool]

_FILENAME_RE = re.compile(r"^(?P<station>[^_]+)_(?P<year>\d{4})_(?P<doy>\d{1,3})\.txt$")


@dataclass(frozen=True)
class StationRow:
    station: str
    time_h: float
    prn: str
    dtec: float
    azimuth: float
    elevation: float
    ipp_lon: float
    ipp_lat: float


@dataclass
class ManifestBuilder:
    source_folder: Path
    filters: ImportFilters
    stations: set[str] = field(default_factory=set)
    prns: set[str] = field(default_factory=set)
    row_counts_by_station: Counter[str] = field(default_factory=Counter)
    row_counts_by_prn: Counter[str] = field(default_factory=Counter)
    total_rows_seen: int = 0
    valid_rows_after_filters: int = 0
    malformed_row_count: int = 0
    non_gps_row_count: int = 0
    low_elevation_row_count: int = 0
    out_of_bounds_row_count: int = 0
    time_min: float | None = None
    time_max: float | None = None
    ipp_lon_min: float | None = None
    ipp_lon_max: float | None = None
    ipp_lat_min: float | None = None
    ipp_lat_max: float | None = None
    detected_years: set[int] = field(default_factory=set)
    detected_doys: set[int] = field(default_factory=set)

    def add_row(self, row: StationRow) -> None:
        self.total_rows_seen += 1
        if not row.prn.startswith(self.filters.constellation_prefix):
            self.non_gps_row_count += 1
            return
        if row.elevation < self.filters.min_elevation_deg:
            self.low_elevation_row_count += 1
            return
        if not (self.filters.lon_min <= row.ipp_lon <= self.filters.lon_max and self.filters.lat_min <= row.ipp_lat <= self.filters.lat_max):
            self.out_of_bounds_row_count += 1
            return
        self.valid_rows_after_filters += 1
        self.stations.add(row.station)
        self.prns.add(row.prn)
        self.row_counts_by_station[row.station] += 1
        self.row_counts_by_prn[row.prn] += 1
        self.time_min = row.time_h if self.time_min is None else min(self.time_min, row.time_h)
        self.time_max = row.time_h if self.time_max is None else max(self.time_max, row.time_h)
        self.ipp_lon_min = row.ipp_lon if self.ipp_lon_min is None else min(self.ipp_lon_min, row.ipp_lon)
        self.ipp_lon_max = row.ipp_lon if self.ipp_lon_max is None else max(self.ipp_lon_max, row.ipp_lon)
        self.ipp_lat_min = row.ipp_lat if self.ipp_lat_min is None else min(self.ipp_lat_min, row.ipp_lat)
        self.ipp_lat_max = row.ipp_lat if self.ipp_lat_max is None else max(self.ipp_lat_max, row.ipp_lat)

    def to_manifest(self) -> dict[str, object]:
        year = next(iter(self.detected_years)) if len(self.detected_years) == 1 else None
        doy = next(iter(self.detected_doys)) if len(self.detected_doys) == 1 else None
        return {
            "source_folder": str(self.source_folder),
            "year": year,
            "doy": doy,
            "station_count": len(self.stations),
            "stations": sorted(self.stations),
            "gps_prns": sorted(self.prns),
            "time_range_hours": {"min": self.time_min, "max": self.time_max},
            "row_counts_by_station": dict(sorted(self.row_counts_by_station.items())),
            "row_counts_by_prn": dict(sorted(self.row_counts_by_prn.items())),
            "total_rows_seen": self.total_rows_seen,
            "valid_rows_after_filters": self.valid_rows_after_filters,
            "malformed_row_count": self.malformed_row_count,
            "non_gps_row_count": self.non_gps_row_count,
            "low_elevation_row_count": self.low_elevation_row_count,
            "out_of_bounds_row_count": self.out_of_bounds_row_count,
            "ipp_bounds": {"lon_min": self.ipp_lon_min, "lon_max": self.ipp_lon_max, "lat_min": self.ipp_lat_min, "lat_max": self.ipp_lat_max},
            "applied_filters": self.filters.as_manifest_dict(),
        }


def station_from_filename(path: Path) -> str:
    return path.name.split("_", 1)[0]


def iter_station_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt")


def parse_row(line: str, station: str) -> StationRow | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = [part.strip() for part in stripped.split(";")]
    if parts and parts[-1] == "":
        parts.pop()
    if len(parts) != 7:
        raise ValueError("Expected 7 semicolon-separated fields")
    return StationRow(
        station=station,
        time_h=float(parts[0]),
        prn=parts[1],
        dtec=float(parts[2]),
        azimuth=float(parts[3]),
        elevation=float(parts[4]),
        ipp_lon=float(parts[5]),
        ipp_lat=float(parts[6]),
    )


def iter_valid_rows(path: Path, filters: ImportFilters | None = None) -> Iterator[StationRow]:
    filters = filters or ImportFilters()
    station = station_from_filename(path)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = parse_row(line, station)
            except ValueError:
                continue
            if row is None:
                continue
            if not row.prn.startswith(filters.constellation_prefix):
                continue
            if row.elevation < filters.min_elevation_deg:
                continue
            if not (filters.lon_min <= row.ipp_lon <= filters.lon_max and filters.lat_min <= row.ipp_lat <= filters.lat_max):
                continue
            yield row


def parse_station_file(path: Path, builder: ManifestBuilder) -> None:
    station = station_from_filename(path)
    match = _FILENAME_RE.match(path.name)
    if match:
        builder.detected_years.add(int(match.group("year")))
        builder.detected_doys.add(int(match.group("doy")))

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = parse_row(line, station)
            except ValueError:
                builder.total_rows_seen += 1
                builder.malformed_row_count += 1
                continue
            if row is not None:
                builder.add_row(row)


def _progress(progress: ProgressCallback | None, stage: str, current: int, total: int, message: str) -> None:
    if progress:
        progress(stage, current, total, message)


def _detect_day(files: list[Path]) -> tuple[set[int], set[int]]:
    years: set[int] = set(); doys: set[int] = set()
    for path in files:
        match = _FILENAME_RE.match(path.name)
        if match:
            years.add(int(match.group("year"))); doys.add(int(match.group("doy")))
    return years, doys


def _fallback_import_file(con, path: Path, filters: ImportFilters) -> tuple[int, int, int, int, int, int]:
    builder = ManifestBuilder(path.parent, filters)
    parse_station_file(path, builder)
    valid = list(iter_valid_rows(path, filters))
    if valid:
        con.executemany("INSERT INTO observations (station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [row_to_record(r, filters) for r in valid])
    return builder.total_rows_seen, len(valid), builder.malformed_row_count, builder.non_gps_row_count, builder.low_elevation_row_count, builder.out_of_bounds_row_count


def _manifest_from_cache(folder: Path, cache_path: Path, filters: ImportFilters) -> dict[str, object]:
    with duckdb.connect(str(cache_path), read_only=True) as con:
        meta = con.execute("SELECT source_folder, year, doy, source_file_count, total_rows_seen, valid_rows_stored FROM metadata LIMIT 1").fetchone()
        stats = con.execute("""
            SELECT COUNT(DISTINCT station), LIST(DISTINCT station ORDER BY station), LIST(DISTINCT prn ORDER BY prn),
                   MIN(time_h), MAX(time_h), MIN(ipp_lon), MAX(ipp_lon), MIN(ipp_lat), MAX(ipp_lat)
            FROM observations
        """).fetchone()
        by_station = dict(con.execute("SELECT station, COUNT(*) FROM observations GROUP BY station ORDER BY station").fetchall())
        by_prn = dict(con.execute("SELECT prn, COUNT(*) FROM observations GROUP BY prn ORDER BY prn").fetchall())
    return {
        "source_folder": str(folder), "year": meta[1], "doy": meta[2], "station_count": int(stats[0] or 0),
        "stations": list(stats[1] or []), "gps_prns": list(stats[2] or []),
        "time_range_hours": {"min": stats[3], "max": stats[4]},
        "row_counts_by_station": by_station, "row_counts_by_prn": by_prn,
        "total_rows_seen": int(meta[4] or 0), "valid_rows_after_filters": int(meta[5] or 0),
        "malformed_row_count": 0, "non_gps_row_count": 0, "low_elevation_row_count": 0, "out_of_bounds_row_count": 0,
        "ipp_bounds": {"lon_min": stats[5], "lon_max": stats[6], "lat_min": stats[7], "lat_max": stats[8]},
        "applied_filters": filters.as_manifest_dict(), "cache_path": str(cache_path),
    }


def build_manifest(folder: Path, cache_dir: Path, filters: ImportFilters | None = None, progress: ProgressCallback | None = None, cancel: CancelCallback | None = None, force_rebuild: bool = False) -> dict[str, object]:
    from datetime import datetime, timezone

    filters = filters or ImportFilters()
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {folder}")

    _progress(progress, "scanning_files", 0, 1, "[1/6] Scanning files")
    files = iter_station_files(folder)
    years, doys = _detect_day(files)
    year = next(iter(years)) if len(years) == 1 else None
    doy = next(iter(doys)) if len(doys) == 1 else None
    cache_path = cache_path_for_day(cache_dir, year, doy, filters)
    _progress(progress, "scanning_files", 1, 1, f"[1/6] Found {len(files)} source files")

    if not force_rebuild and cache_is_valid(cache_path, folder, files, year, doy, filters):
        _progress(progress, "finalizing_cache", 1, 1, "[6/6] Existing daily cache loaded")
        return _manifest_from_cache(folder, cache_path, filters)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        cache_path.unlink()
    total_seen = 0; valid_stored = 0; malformed = 0; non_gps = 0; low_elev = 0; out_bounds = 0
    with duckdb.connect(str(cache_path)) as con:
        configure_connection(con); create_schema(con); create_metadata_table(con)
        con.execute("DELETE FROM metadata")
        con.execute("INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [str(folder), year, doy, filters.min_elevation_deg, filters.lon_min, filters.lon_max, filters.lat_min, filters.lat_max, len(files), 0, 0, datetime.now(timezone.utc).isoformat(), False, CACHE_VERSION])
        for index, path in enumerate(files, start=1):
            if cancel and cancel():
                con.execute("UPDATE metadata SET completed=false, total_rows_seen=?, valid_rows_stored=?", [total_seen, valid_stored])
                raise RuntimeError("Import cancelled")
            _progress(progress, "reading_filtering", index, len(files), f"[2/6] Reading file {index} of {len(files)}: {path.name}")
            before = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            try:
                safe_count_path = str(path).replace("'", "''")
                parsed_sql = f"read_csv('{safe_count_path}', delim=';', header=false, columns={{'time_h':'DOUBLE','prn':'VARCHAR','dtec':'DOUBLE','azimuth':'DOUBLE','elevation':'DOUBLE','ipp_lon':'DOUBLE','ipp_lat':'DOUBLE','extra':'VARCHAR'}}, null_padding=true, ignore_errors=true, auto_detect=false)"
                seen = sum(1 for line in path.open('r', encoding='utf-8') if line.strip())
                valid_expr = "time_h IS NOT NULL AND prn IS NOT NULL AND dtec IS NOT NULL AND azimuth IS NOT NULL AND elevation IS NOT NULL AND ipp_lon IS NOT NULL AND ipp_lat IS NOT NULL"
                parsed = con.execute(f"SELECT COUNT(*) FROM {parsed_sql} WHERE {valid_expr}").fetchone()[0]
                malformed += int(seen - parsed)
                non_gps += int(con.execute(f"SELECT COUNT(*) FROM {parsed_sql} WHERE {valid_expr} AND NOT starts_with(prn, ?)", [filters.constellation_prefix]).fetchone()[0])
                low_elev += int(con.execute(f"SELECT COUNT(*) FROM {parsed_sql} WHERE {valid_expr} AND starts_with(prn, ?) AND elevation < ?", [filters.constellation_prefix, filters.min_elevation_deg]).fetchone()[0])
                out_bounds += int(con.execute(f"SELECT COUNT(*) FROM {parsed_sql} WHERE {valid_expr} AND starts_with(prn, ?) AND elevation >= ? AND NOT (ipp_lon BETWEEN ? AND ? AND ipp_lat BETWEEN ? AND ?)", [filters.constellation_prefix, filters.min_elevation_deg, filters.lon_min, filters.lon_max, filters.lat_min, filters.lat_max]).fetchone()[0])
                con.execute(f"INSERT INTO observations SELECT * FROM ({source_file_sql(path, station_from_filename(path), filters)})")
                status = "imported"; err = ""
            except duckdb.Error as exc:
                seen, _, bad, ng, le, ob = _fallback_import_file(con, path, filters); malformed += bad; non_gps += ng; low_elev += le; out_bounds += ob
                status = "fallback"; err = str(exc)[:500]
            after = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            valid = int(after - before); total_seen += int(seen); valid_stored += valid
            con.execute("INSERT OR REPLACE INTO imported_files VALUES (?, ?, ?, ?, ?)", [path.name, int(seen), valid, status, err])
            con.execute("UPDATE metadata SET total_rows_seen=?, valid_rows_stored=?", [total_seen, valid_stored])
            _progress(progress, "writing_database", valid_stored, max(valid_stored, 1), f"[3/6] Stored {valid_stored:,} filtered observations")
        _progress(progress, "building_indexes", 0, 1, "[4/6] Building epoch table for GPS PRNs")
        finalize_cache(con)
        _progress(progress, "building_indexes", 1, 1, "[4/6] Built epoch table and indexes")
        _progress(progress, "visibility_arcs", 1, 1, "[5/6] Computing visibility arcs")
        con.execute("UPDATE metadata SET completed=true, total_rows_seen=?, valid_rows_stored=?", [total_seen, valid_stored])
    _progress(progress, "finalizing_cache", 1, 1, "[6/6] Finalizing manifest")
    manifest = _manifest_from_cache(folder, cache_path, filters)
    manifest["malformed_row_count"] = malformed
    manifest["non_gps_row_count"] = non_gps
    manifest["low_elevation_row_count"] = low_elev
    manifest["out_of_bounds_row_count"] = out_bounds
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "day_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _progress(progress, "done", 1, 1, "Done" if valid_stored else "Import completed, but no valid rows passed filters.")
    return manifest
