from __future__ import annotations

import json
import re

import duckdb
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from tid_analyzer.config import ImportFilters
from tid_analyzer.stations.catalog import resolve_stations, station_code_from_filename
from tid_analyzer.importer.cache import CACHE_VERSION, cache_is_valid, cache_path_for_day, configure_connection, create_metadata_table, create_schema, finalize_cache, source_file_sql, row_to_record, parsed_relation_sql, parsed_valid_expr, normalized_prn_expr

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
    return station_code_from_filename(path)


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
        prn=parts[1].upper(),
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



def extract_station_codes(files: list[Path]) -> list[str]:
    return sorted({station_from_filename(path) for path in files})


def populate_stations_table(con, station_codes: list[str], cache_dir: Path) -> tuple[int, int]:
    rows = resolve_stations(station_codes, cache_dir)
    con.executemany(
        "INSERT OR REPLACE INTO stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r.station, r.full_site_id, r.longitude, r.latitude, r.height, r.x, r.y, r.z, r.coordinate_source, r.reference_frame, r.coordinate_epoch, r.resolved, r.resolution_note) for r in rows],
    )
    return sum(1 for r in rows if r.resolved), len(rows)


@dataclass
class ImportCounters:
    total_nonempty_rows: int = 0
    parsed_rows: int = 0
    malformed_rows: int = 0
    non_gps_rows: int = 0
    low_elevation_rows: int = 0
    out_of_bounds_rows: int = 0
    valid_rows_stored: int = 0

    @property
    def gps_rows(self) -> int:
        return self.parsed_rows - self.non_gps_rows

    def add(self, other: "ImportCounters") -> None:
        self.total_nonempty_rows += other.total_nonempty_rows
        self.parsed_rows += other.parsed_rows
        self.malformed_rows += other.malformed_rows
        self.non_gps_rows += other.non_gps_rows
        self.low_elevation_rows += other.low_elevation_rows
        self.out_of_bounds_rows += other.out_of_bounds_rows
        self.valid_rows_stored += other.valid_rows_stored

    def final_categories_total(self) -> int:
        return self.malformed_rows + self.non_gps_rows + self.low_elevation_rows + self.out_of_bounds_rows + self.valid_rows_stored

    def validate(self, label: str) -> None:
        final = self.final_categories_total()
        if final != self.total_nonempty_rows:
            raise RuntimeError(f"Import counter mismatch for {label}: final categories total {final} != total non-empty rows {self.total_nonempty_rows}")


def _count_nonempty_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _count_duckdb_file(con, path: Path, filters: ImportFilters) -> ImportCounters:
    source = f"({parsed_relation_sql(path)})"
    valid = parsed_valid_expr(); prn = normalized_prn_expr(); prefix = filters.constellation_prefix
    counters = ImportCounters(total_nonempty_rows=_count_nonempty_rows(path))
    counters.parsed_rows = int(con.execute(f"SELECT COUNT(*) FROM {source} WHERE {valid}").fetchone()[0])
    counters.malformed_rows = counters.total_nonempty_rows - counters.parsed_rows
    counters.non_gps_rows = int(con.execute(f"SELECT COUNT(*) FROM {source} WHERE {valid} AND NOT starts_with({prn}, upper(trim(?)))", [prefix]).fetchone()[0])
    counters.low_elevation_rows = int(con.execute(f"SELECT COUNT(*) FROM {source} WHERE {valid} AND starts_with({prn}, upper(trim(?))) AND elevation < ?", [prefix, filters.min_elevation_deg]).fetchone()[0])
    counters.out_of_bounds_rows = int(con.execute(f"SELECT COUNT(*) FROM {source} WHERE {valid} AND starts_with({prn}, upper(trim(?))) AND elevation >= ? AND NOT (ipp_lon BETWEEN ? AND ? AND ipp_lat BETWEEN ? AND ?)", [prefix, filters.min_elevation_deg, filters.lon_min, filters.lon_max, filters.lat_min, filters.lat_max]).fetchone()[0])
    return counters

def _fallback_import_file(con, path: Path, filters: ImportFilters) -> ImportCounters:
    counters = ImportCounters()
    valid: list[StationRow] = []
    station = station_from_filename(path)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            counters.total_nonempty_rows += 1
            try:
                row = parse_row(line, station)
            except ValueError:
                counters.malformed_rows += 1
                continue
            if row is None:
                continue
            counters.parsed_rows += 1
            if not row.prn.startswith(filters.constellation_prefix.upper()):
                counters.non_gps_rows += 1
            elif row.elevation < filters.min_elevation_deg:
                counters.low_elevation_rows += 1
            elif not (filters.lon_min <= row.ipp_lon <= filters.lon_max and filters.lat_min <= row.ipp_lat <= filters.lat_max):
                counters.out_of_bounds_rows += 1
            else:
                valid.append(row)
    if valid:
        con.executemany("INSERT INTO observations (station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [row_to_record(r, filters) for r in valid])
    counters.valid_rows_stored = len(valid)
    counters.validate(path.name)
    return counters

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
        diag = con.execute("""
            SELECT COALESCE(SUM(total_nonempty_rows), 0), COALESCE(SUM(parsed_rows), 0),
                   COALESCE(SUM(malformed_rows), 0), COALESCE(SUM(non_gps_rows), 0),
                   COALESCE(SUM(low_elevation_rows), 0), COALESCE(SUM(out_of_bounds_rows), 0),
                   COALESCE(SUM(valid_row_count), 0)
            FROM imported_files
        """).fetchone()
    parsed_rows = int(diag[1] or 0)
    non_gps_rows = int(diag[3] or 0)
    return {
        "source_folder": str(folder), "year": meta[1], "doy": meta[2], "station_count": int(stats[0] or 0),
        "stations": list(stats[1] or []), "gps_prns": list(stats[2] or []),
        "time_range_hours": {"min": stats[3], "max": stats[4]},
        "row_counts_by_station": by_station, "row_counts_by_prn": by_prn,
        "total_rows_seen": int(meta[4] or 0), "valid_rows_after_filters": int(meta[5] or 0),
        "parsed_row_count": parsed_rows,
        "malformed_row_count": int(diag[2] or 0), "non_gps_row_count": non_gps_rows,
        "low_elevation_row_count": int(diag[4] or 0), "out_of_bounds_row_count": int(diag[5] or 0),
        "ipp_bounds": {"lon_min": stats[5], "lon_max": stats[6], "lat_min": stats[7], "lat_max": stats[8]},
        "import_diagnostics": {
            "total_nonempty_rows": int(diag[0] or 0),
            "parsed_rows": parsed_rows,
            "malformed_rows": int(diag[2] or 0),
            "non_gps_rows": non_gps_rows,
            "gps_rows": parsed_rows - non_gps_rows,
            "low_elevation_rows": int(diag[4] or 0),
            "out_of_bounds_rows": int(diag[5] or 0),
            "valid_rows_stored": int(diag[6] or 0),
        },
        "applied_filters": filters.as_manifest_dict(), "cache_path": str(cache_path),
    }


def build_manifest(folder: Path, cache_dir: Path, filters: ImportFilters | None = None, progress: ProgressCallback | None = None, cancel: CancelCallback | None = None, force_rebuild: bool = False) -> dict[str, object]:
    from datetime import datetime, timezone

    filters = filters or ImportFilters()
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {folder}")

    _progress(progress, "scanning_files", 0, 1, "[1/7] Scanning files")
    files = iter_station_files(folder)
    years, doys = _detect_day(files)
    year = next(iter(years)) if len(years) == 1 else None
    doy = next(iter(doys)) if len(doys) == 1 else None
    cache_path = cache_path_for_day(cache_dir, year, doy, filters)
    _progress(progress, "scanning_files", 1, 1, f"[1/7] Found {len(files)} source files")
    station_codes = extract_station_codes(files)

    if not force_rebuild and cache_is_valid(cache_path, folder, files, year, doy, filters):
        _progress(progress, "finalizing_cache", 1, 1, "[7/7] Existing daily cache loaded")
        return _manifest_from_cache(folder, cache_path, filters)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        cache_path.unlink()
    totals = ImportCounters()
    with duckdb.connect(str(cache_path)) as con:
        configure_connection(con)
        _progress(progress, "resolving_stations", 0, max(len(station_codes), 1), "[2/7] Resolving station coordinates")
        create_schema(con); create_metadata_table(con)
        resolved_count, station_total = populate_stations_table(con, station_codes, cache_dir)
        _progress(progress, "resolving_stations", resolved_count, max(station_total, 1), f"[2/7] Resolved {resolved_count} of {station_total} station coordinates")
        _progress(progress, "preparing_database", 0, 1, "[3/7] Preparing daily database")
        if not files:
            raise ValueError("No .txt source files found in input folder")
        unexpected = [path.name for path in files if not _FILENAME_RE.match(path.name)]
        if unexpected:
            _progress(progress, "preparing_database", 0, 1, f"[3/7] Preparing daily database; {len(unexpected)} filename(s) do not match STATION_YYYY_DOY.txt")
        con.execute("DELETE FROM metadata")
        con.execute("INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [str(folder), year, doy, filters.min_elevation_deg, filters.lon_min, filters.lon_max, filters.lat_min, filters.lat_max, len(files), 0, 0, datetime.now(timezone.utc).isoformat(), False, CACHE_VERSION])
        _progress(progress, "preparing_database", 1, 1, "[3/7] Daily database ready")
        for index, path in enumerate(files, start=1):
            if cancel and cancel():
                con.execute("UPDATE metadata SET completed=false, total_rows_seen=?, valid_rows_stored=?", [totals.total_nonempty_rows, totals.valid_rows_stored])
                raise RuntimeError("Import cancelled")
            _progress(progress, "reading_filtering", index - 1, len(files), f"[4/7] Reading file {index} of {len(files)}: {path.name}")
            before = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            try:
                counters = _count_duckdb_file(con, path, filters)
                con.execute(f"INSERT INTO observations SELECT * FROM ({source_file_sql(path, station_from_filename(path), filters)})")
                status = "imported"; err = ""
            except duckdb.Error as exc:
                counters = _fallback_import_file(con, path, filters)
                status = "fallback"; err = str(exc)[:500]
            after = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            counters.valid_rows_stored = int(after - before)
            counters.validate(path.name)
            totals.add(counters)
            con.execute("INSERT OR REPLACE INTO imported_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [path.name, counters.total_nonempty_rows, counters.parsed_rows, counters.malformed_rows, counters.non_gps_rows, counters.low_elevation_rows, counters.out_of_bounds_rows, counters.valid_rows_stored, status, err])
            con.execute("UPDATE metadata SET total_rows_seen=?, valid_rows_stored=?", [totals.total_nonempty_rows, totals.valid_rows_stored])
            rejected = totals.malformed_rows + totals.non_gps_rows + totals.low_elevation_rows + totals.out_of_bounds_rows
            _progress(progress, "reading_filtering", index, len(files), f"[4/7] Reading file {index} of {len(files)}: {path.name}. Stored {totals.valid_rows_stored:,} valid observations; rejected {totals.low_elevation_rows:,} below elevation; {rejected:,} total rejected.")
        try:
            totals.validate("full import")
        except RuntimeError:
            con.execute("UPDATE metadata SET completed=false, total_rows_seen=?, valid_rows_stored=?", [totals.total_nonempty_rows, totals.valid_rows_stored])
            raise
        _progress(progress, "building_indexes", 0, 1, "[5/7] Building PRN/epoch indexes")
        finalize_cache(con)
        _progress(progress, "building_indexes", 1, 1, "[5/7] Built PRN/epoch indexes")
        _progress(progress, "visibility_arcs", 1, 1, "[6/7] Computing satellite visibility arcs")
        if totals.total_nonempty_rows == 0:
            con.execute("UPDATE metadata SET completed=false, total_rows_seen=0, valid_rows_stored=0")
            raise RuntimeError("Input files are empty: no non-empty source rows were found.")
        if totals.valid_rows_stored == 0:
            con.execute("UPDATE metadata SET completed=false, total_rows_seen=?, valid_rows_stored=0", [totals.total_nonempty_rows])
            raise RuntimeError(f"No valid observations passed the full import filters after reading all source rows. Parsed: {totals.parsed_rows}; malformed: {totals.malformed_rows}; GPS: {totals.gps_rows}; low elevation: {totals.low_elevation_rows}; out of bounds: {totals.out_of_bounds_rows}.")
        con.execute("UPDATE metadata SET completed=true, total_rows_seen=?, valid_rows_stored=?", [totals.total_nonempty_rows, totals.valid_rows_stored])
    _progress(progress, "finalizing_cache", 1, 1, "[7/7] Finalizing cache")
    manifest = _manifest_from_cache(folder, cache_path, filters)
    manifest["parsed_row_count"] = totals.parsed_rows
    manifest["malformed_row_count"] = totals.malformed_rows
    manifest["non_gps_row_count"] = totals.non_gps_rows
    manifest["low_elevation_row_count"] = totals.low_elevation_rows
    manifest["out_of_bounds_row_count"] = totals.out_of_bounds_rows
    manifest["import_diagnostics"] = {
        "total_nonempty_rows": totals.total_nonempty_rows,
        "parsed_rows": totals.parsed_rows,
        "malformed_rows": totals.malformed_rows,
        "non_gps_rows": totals.non_gps_rows,
        "gps_rows": totals.gps_rows,
        "low_elevation_rows": totals.low_elevation_rows,
        "out_of_bounds_rows": totals.out_of_bounds_rows,
        "valid_rows_stored": totals.valid_rows_stored,
    }
    manifest["completed"] = True
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "day_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _progress(progress, "done", 1, 1, "Done")
    return manifest
