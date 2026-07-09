from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from tid_analyzer.config import ImportFilters

ProgressCallback = Callable[[str, int, int, str], None]

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


def build_manifest(folder: Path, cache_dir: Path, filters: ImportFilters | None = None, progress: ProgressCallback | None = None) -> dict[str, object]:
    filters = filters or ImportFilters()
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {folder}")

    progress and progress("scanning_files", 0, 0, "Scanning files")
    files = iter_station_files(folder)
    builder = ManifestBuilder(source_folder=folder, filters=filters)
    total = len(files)
    for index, path in enumerate(files, start=1):
        progress and progress("parsing_files", index, total, f"Parsing file {index} / {total}: {path.name}")
        parse_station_file(path, builder)

    manifest = builder.to_manifest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "day_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    message = "Done" if builder.valid_rows_after_filters else "Import completed, but no valid rows passed filters. Check parser format, constellation, elevation, and map bounds."
    progress and progress("done", total, total, message)
    return manifest
