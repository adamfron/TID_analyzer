from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tid_analyzer.config import ImportFilters

ProgressCallback = Callable[[str, int, int, str], None]

_FILENAME_RE = re.compile(r"^(?P<station>[^_]+)_(?P<year>\d{4})_(?P<doy>\d{1,3})\.txt$")


@dataclass
class ManifestBuilder:
    source_folder: Path
    filters: ImportFilters
    stations: set[str] = field(default_factory=set)
    prns: set[str] = field(default_factory=set)
    row_counts_by_station: Counter[str] = field(default_factory=Counter)
    row_counts_by_prn: Counter[str] = field(default_factory=Counter)
    malformed_row_count: int = 0
    time_min: float | None = None
    time_max: float | None = None
    ipp_lon_min: float | None = None
    ipp_lon_max: float | None = None
    ipp_lat_min: float | None = None
    ipp_lat_max: float | None = None
    detected_years: set[int] = field(default_factory=set)
    detected_doys: set[int] = field(default_factory=set)

    def add_row(self, station: str, prn: str, time_hours: float, elevation: float, lon: float, lat: float) -> None:
        if not prn.startswith(self.filters.constellation_prefix) or elevation < self.filters.min_elevation_deg:
            return
        self.stations.add(station)
        self.prns.add(prn)
        self.row_counts_by_station[station] += 1
        self.row_counts_by_prn[prn] += 1
        self.time_min = time_hours if self.time_min is None else min(self.time_min, time_hours)
        self.time_max = time_hours if self.time_max is None else max(self.time_max, time_hours)
        self.ipp_lon_min = lon if self.ipp_lon_min is None else min(self.ipp_lon_min, lon)
        self.ipp_lon_max = lon if self.ipp_lon_max is None else max(self.ipp_lon_max, lon)
        self.ipp_lat_min = lat if self.ipp_lat_min is None else min(self.ipp_lat_min, lat)
        self.ipp_lat_max = lat if self.ipp_lat_max is None else max(self.ipp_lat_max, lat)

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
            "malformed_row_count": self.malformed_row_count,
            "ipp_bounds": {
                "lon_min": self.ipp_lon_min,
                "lon_max": self.ipp_lon_max,
                "lat_min": self.ipp_lat_min,
                "lat_max": self.ipp_lat_max,
            },
            "applied_filters": self.filters.as_manifest_dict(),
        }


def station_from_filename(path: Path) -> str:
    return path.name.split("_", 1)[0]


def iter_station_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt")


def parse_station_file(path: Path, builder: ManifestBuilder) -> None:
    station = station_from_filename(path)
    match = _FILENAME_RE.match(path.name)
    if match:
        builder.detected_years.add(int(match.group("year")))
        builder.detected_doys.add(int(match.group("doy")))

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            parts = [part.strip() for part in stripped.split(";")]
            if len(parts) != 7:
                builder.malformed_row_count += 1
                continue
            try:
                time_hours = float(parts[0])
                prn = parts[1]
                elevation = float(parts[4])
                lon = float(parts[5])
                lat = float(parts[6])
            except ValueError:
                builder.malformed_row_count += 1
                continue
            builder.add_row(station, prn, time_hours, elevation, lon, lat)


def build_manifest(
    folder: Path,
    cache_dir: Path,
    filters: ImportFilters | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    filters = filters or ImportFilters()
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {folder}")

    progress and progress("scanning_files", 0, 0, "Scanning station files")
    files = iter_station_files(folder)
    builder = ManifestBuilder(source_folder=folder, filters=filters)
    total = len(files)
    for index, path in enumerate(files, start=1):
        progress and progress("parsing_station_files", index - 1, total, f"Parsing {path.name}")
        parse_station_file(path, builder)
        progress and progress("applying_filters", index, total, f"Applied GPS/elevation filters for {path.name}")

    progress and progress("collecting_metadata", total, total, "Collecting metadata")
    manifest = builder.to_manifest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "day_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress and progress("done", total, total, "Import complete")
    return manifest
