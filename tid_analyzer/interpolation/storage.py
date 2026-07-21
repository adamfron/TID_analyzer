"""Persistent Zarr storage for interpolated one-epoch grids.

This module only manages storage and retrieval.  It deliberately avoids batch
orchestration, background jobs, HTTP endpoints, and frontend integration.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import zarr
from numcodecs import Blosc

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import CACHE_VERSION, cache_path_for_day
from tid_analyzer.interpolation.natural_neighbor import (
    DEFAULT_GRID_STEP_DEG,
    LAT_BOUNDS,
    LON_BOUNDS,
    METHOD,
    PROJECTION,
    validate_grid_step,
    NaturalNeighborResult,
)

CACHE_FORMAT_VERSION = "interpolation_zarr_v1"
ELIGIBILITY_RULE_VERSION = "visibility_arc_rules_v1"
VALID_EPOCH_STATUSES = {"pending", "ready", "insufficient_points", "geometry_error", "failed"}


@dataclass(frozen=True)
class ArcDescriptor:
    prn: str
    arc_index: int
    expected_epoch_count: int


@dataclass(frozen=True)
class CacheValidation:
    compatible: bool
    reason: str = ""
    stale: bool = False
    metadata: dict[str, Any] | None = None


class EpochGridLRU:
    """Tiny in-process LRU for recently read epoch payloads."""

    def __init__(self, max_size: int = 5) -> None:
        self.max_size = max(0, int(max_size))
        self._items: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()

    def get(self, key: tuple[str, int, int]) -> dict[str, Any] | None:
        if self.max_size <= 0 or key not in self._items:
            return None
        value = self._items.pop(key)
        self._items[key] = value
        return _copy_payload(value)

    def put(self, key: tuple[str, int, int], value: dict[str, Any]) -> None:
        if self.max_size <= 0:
            return
        self._items[key] = _copy_payload(value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


def grid_cache_namespace(grid_step_deg: float) -> str:
    step = validate_grid_step(grid_step_deg)
    return f"grid_{str(step).replace('.', 'p')}"

def interpolation_cache_dir(cache_root: Path, year: int, doy: int, minimum_elevation_deg: float, grid_step_deg: float = DEFAULT_GRID_STEP_DEG) -> Path:
    filters = ImportFilters(min_elevation_deg=minimum_elevation_deg)
    return cache_path_for_day(cache_root, year, doy, filters).parent / "interpolation" / grid_cache_namespace(grid_step_deg)


def create_source_fingerprint(
    *,
    daily_cache_path: Path,
    year: int,
    doy: int,
    minimum_elevation_deg: float,
    grid_step_deg: float = DEFAULT_GRID_STEP_DEG,
    longitude_bounds: tuple[float, float] = LON_BOUNDS,
    latitude_bounds: tuple[float, float] = LAT_BOUNDS,
    projection: str = PROJECTION,
    interpolation_method: str = METHOD,
    eligibility_rule_version: str = ELIGIBILITY_RULE_VERSION,
) -> dict[str, Any]:
    valid_observations = 0
    source_file_count = 0
    source_cache_version = CACHE_VERSION
    if daily_cache_path.exists():
        with duckdb.connect(str(daily_cache_path), read_only=True) as con:
            valid_observations = int(con.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            row = con.execute("SELECT source_file_count, application_cache_version FROM metadata LIMIT 1").fetchone()
            if row:
                source_file_count = int(row[0] or 0)
                source_cache_version = str(row[1] or CACHE_VERSION)
    return {
        "source_cache_version": source_cache_version,
        "year": int(year),
        "doy": int(doy),
        "minimum_elevation_deg": float(minimum_elevation_deg),
        "valid_observation_count": valid_observations,
        "source_file_count": source_file_count,
        "interpolation_method": interpolation_method,
        "grid_step_deg": float(grid_step_deg),
        "longitude_bounds": list(map(float, longitude_bounds)),
        "latitude_bounds": list(map(float, latitude_bounds)),
        "projection": projection,
        "eligibility_rule_version": eligibility_rule_version,
    }


def create_or_open_interpolation_cache(
    *,
    cache_root: Path,
    daily_cache_path: Path | None = None,
    year: int,
    doy: int,
    minimum_elevation_deg: float,
    arcs: Iterable[ArcDescriptor | dict[str, Any]] = (),
    grid_step_deg: float = DEFAULT_GRID_STEP_DEG,
    longitude_bounds: tuple[float, float] = LON_BOUNDS,
    latitude_bounds: tuple[float, float] = LAT_BOUNDS,
    projection: str = PROJECTION,
    interpolation_method: str = METHOD,
    minimum_arc_station_count: int = 3,
    minimum_arc_duration_min: float = 0.0,
    minimum_epoch_ipp_count: int = 30,
) -> Path:
    grid_step_deg = validate_grid_step(grid_step_deg)
    cache_dir = interpolation_cache_dir(cache_root, year, doy, minimum_elevation_deg, grid_step_deg)
    cache_dir.mkdir(parents=True, exist_ok=True)
    daily_cache_path = daily_cache_path or cache_path_for_day(cache_root, year, doy, ImportFilters(min_elevation_deg=minimum_elevation_deg))
    fingerprint = create_source_fingerprint(daily_cache_path=daily_cache_path, year=year, doy=doy, minimum_elevation_deg=minimum_elevation_deg, grid_step_deg=grid_step_deg, longitude_bounds=longitude_bounds, latitude_bounds=latitude_bounds, projection=projection, interpolation_method=interpolation_method)
    metadata_path = cache_dir / "metadata.json"
    now = _now()
    if metadata_path.exists():
        metadata = _read_metadata(metadata_path)
        metadata["updated_at"] = now
    else:
        metadata = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "source_cache_version": fingerprint["source_cache_version"],
            "source_cache_fingerprint": fingerprint,
            "year": int(year), "doy": int(doy), "minimum_elevation_deg": float(minimum_elevation_deg),
            "longitude_bounds": list(map(float, longitude_bounds)), "latitude_bounds": list(map(float, latitude_bounds)),
            "grid_step_deg": float(grid_step_deg), "projection": projection, "interpolation_method": interpolation_method,
            "minimum_arc_station_count": int(minimum_arc_station_count), "minimum_arc_duration_min": float(minimum_arc_duration_min), "minimum_epoch_ipp_count": int(minimum_epoch_ipp_count),
            "created_at": now, "updated_at": now, "completed": False, "stale": False, "stale_reason": "", "arc_entries": [],
        }
    existing = {(e["prn"], int(e["arc_index"])): e for e in metadata.get("arc_entries", [])}
    for arc in arcs:
        item = arc if isinstance(arc, dict) else arc.__dict__
        prn, arc_index = str(item["prn"]), int(item["arc_index"])
        existing.setdefault((prn, arc_index), {"prn": prn, "arc_index": arc_index, "expected_epoch_count": int(item["expected_epoch_count"]), "stored_epoch_count": 0, "failed_epoch_count": 0, "status": "pending", "store_path": f"{prn}_arc_{arc_index}.zarr"})
    metadata["minimum_epoch_ipp_count"] = int(minimum_epoch_ipp_count)
    metadata["arc_entries"] = sorted(existing.values(), key=lambda e: (e["prn"], int(e["arc_index"])))
    _write_metadata(metadata_path, metadata)
    return cache_dir


def validate_interpolation_cache(cache_dir: Path, *, expected_fingerprint: dict[str, Any] | None = None, **expected: Any) -> CacheValidation:
    metadata_path = cache_dir / "metadata.json"
    if not metadata_path.exists():
        return CacheValidation(False, "metadata.json is missing")
    metadata = _read_metadata(metadata_path)
    if metadata.get("cache_format_version") != CACHE_FORMAT_VERSION:
        return _stale(cache_dir, metadata, "cache format version mismatch")
    if expected_fingerprint is not None and metadata.get("source_cache_fingerprint") != expected_fingerprint:
        return _stale(cache_dir, metadata, "source cache fingerprint mismatch")
    for key, value in expected.items():
        if value is not None and metadata.get(key) != value:
            return _stale(cache_dir, metadata, f"{key} mismatch")
    if metadata.get("stale"):
        return CacheValidation(False, str(metadata.get("stale_reason") or "cache marked stale"), True, metadata)
    return CacheValidation(True, metadata=metadata)


def write_epoch_result(cache_dir: Path, result: NaturalNeighborResult | dict[str, Any], *, arc_index: int) -> None:
    r = result if isinstance(result, dict) else result.__dict__
    status = str(r["status"])
    if status not in VALID_EPOCH_STATUSES:
        raise ValueError(f"Invalid epoch status: {status}")
    group = _open_arc_group(cache_dir, str(r["prn"]), arc_index, len(r["lat_values"]), len(r["lon_values"]), int(r["epoch_index"]))
    idx = int(r["epoch_index"])
    group["epoch_index"][idx] = idx
    group["time_h"][idx] = float(r["time_h"])
    group["lat_values"][:] = np.asarray(r["lat_values"], dtype=np.float64)
    group["lon_values"][:] = np.asarray(r["lon_values"], dtype=np.float64)
    group["dtec_grid"][idx, :, :] = np.asarray(r["values"], dtype=np.float32)
    group["valid_mask"][idx, :, :] = np.asarray(r["valid_mask"], dtype=bool)
    group["station_count"][idx] = int(r["station_count"])
    group["point_count"][idx] = int(r["point_count"])
    group["message"][idx] = str(r.get("message", ""))
    group.store.close() if hasattr(group.store, "close") else None
    group = zarr.open_group(str(cache_dir / f"{r['prn']}_arc_{arc_index}.zarr"), mode="a")
    group["status"][idx] = status
    _refresh_arc_metadata(cache_dir, str(r["prn"]), arc_index)


def write_epoch_results(cache_dir: Path, results: Iterable[NaturalNeighborResult | dict[str, Any]], *, arc_index: int | None = None) -> None:
    for result in results:
        r = result if isinstance(result, dict) else result.__dict__
        write_epoch_result(cache_dir, result, arc_index=int(arc_index if arc_index is not None else r["arc_index"]))


def read_arc_statuses(cache_dir: Path, *, prn: str, arc_index: int) -> dict[int, str]:
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    if not path.exists():
        return {}
    group = zarr.open_group(str(path), mode="r")
    return {idx: _decode(value) for idx, value in enumerate(group["status"][:])}


def has_epoch_result(cache_dir: Path, *, prn: str, arc_index: int, epoch_index: int) -> bool:
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    if not path.exists():
        return False
    group = zarr.open_group(str(path), mode="r")
    return epoch_index < group["status"].shape[0] and _decode(group["status"][epoch_index]) == "ready"


def read_epoch_result(cache_dir: Path, *, prn: str, arc_index: int, epoch_index: int, lru: EpochGridLRU | None = None) -> dict[str, Any]:
    key = (prn, int(arc_index), int(epoch_index))
    if lru and (cached := lru.get(key)) is not None:
        return cached
    metadata = _read_metadata(cache_dir / "metadata.json")
    validation = validate_interpolation_cache(cache_dir)
    if not validation.compatible:
        raise RuntimeError(f"Interpolation cache is incompatible: {validation.reason}")
    group = zarr.open_group(str(cache_dir / f"{prn}_arc_{arc_index}.zarr"), mode="r")
    idx = int(epoch_index)
    payload = {
        "prn": prn, "arc_index": int(arc_index), "epoch_index": int(group["epoch_index"][idx]), "time_h": float(group["time_h"][idx]),
        "method": metadata["interpolation_method"], "projection": metadata["projection"], "grid_step_deg": float(metadata["grid_step_deg"]),
        "lon_values": group["lon_values"][:], "lat_values": group["lat_values"][:], "values": group["dtec_grid"][idx, :, :],
        "valid_mask": group["valid_mask"][idx, :, :], "point_count": int(group["point_count"][idx]), "station_count": int(group["station_count"][idx]),
        "status": _decode(group["status"][idx]), "message": _decode(group["message"][idx]),
    }
    if lru:
        lru.put(key, payload)
    return payload


def list_missing_epochs(cache_dir: Path, *, prn: str, arc_index: int, expected_epoch_count: int | None = None, retry_failed: bool = False) -> list[int]:
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    if not path.exists():
        return list(range(int(expected_epoch_count or 0)))
    group = zarr.open_group(str(path), mode="r")
    count = int(expected_epoch_count or group["status"].shape[0])
    missing = []
    for idx in range(count):
        status = _decode(group["status"][idx]) if idx < group["status"].shape[0] else "pending"
        if status == "pending" or (retry_failed and status in {"failed", "geometry_error", "insufficient_points"}):
            missing.append(idx)
    return missing


def mark_arc_complete(cache_dir: Path, *, prn: str, arc_index: int) -> None:
    metadata = _read_metadata(cache_dir / "metadata.json")
    for entry in metadata.get("arc_entries", []):
        if entry.get("prn") == prn and int(entry.get("arc_index")) == int(arc_index):
            entry["status"] = "complete"
    metadata["updated_at"] = _now()
    _write_metadata(cache_dir / "metadata.json", metadata)


def mark_cache_complete(cache_dir: Path) -> None:
    metadata = _read_metadata(cache_dir / "metadata.json")
    metadata["completed"] = True
    metadata["updated_at"] = _now()
    _write_metadata(cache_dir / "metadata.json", metadata)


def invalidate_interpolation_cache(cache_dir: Path, reason: str) -> None:
    metadata = _read_metadata(cache_dir / "metadata.json")
    _stale(cache_dir, metadata, reason)


def _open_arc_group(cache_dir: Path, prn: str, arc_index: int, n_lat: int, n_lon: int, epoch_index: int):
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    group = zarr.open_group(str(path), mode="a")
    n_epochs = max(epoch_index + 1, group["status"].shape[0] if "status" in group else 0)
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    def require(name, shape, dtype, chunks=None, fill_value=0, object_codec=None):
        if name in group:
            arr = group[name]
            if arr.shape[0] < shape[0]:
                arr.resize(shape)
            return arr
        kwargs = {"shape": shape, "dtype": dtype, "chunks": chunks or shape, "fill_value": fill_value, "compressor": compressor}
        if object_codec is not None:
            kwargs["object_codec"] = object_codec
        return group.create_dataset(name, **kwargs)
    require("epoch_index", (n_epochs,), np.int64, (max(1, min(n_epochs, 1024)),), -1)
    require("time_h", (n_epochs,), np.float64, (max(1, min(n_epochs, 1024)),), np.nan)
    require("lat_values", (n_lat,), np.float64, (n_lat,), 0.0)
    require("lon_values", (n_lon,), np.float64, (n_lon,), 0.0)
    require("dtec_grid", (n_epochs, n_lat, n_lon), np.float32, (1, n_lat, n_lon), np.nan)
    require("valid_mask", (n_epochs, n_lat, n_lon), bool, (1, n_lat, n_lon), False)
    require("station_count", (n_epochs,), np.int64, (max(1, min(n_epochs, 1024)),), 0)
    require("point_count", (n_epochs,), np.int64, (max(1, min(n_epochs, 1024)),), 0)
    require("status", (n_epochs,), "U32", (max(1, min(n_epochs, 1024)),), "pending")
    require("message", (n_epochs,), "U512", (max(1, min(n_epochs, 1024)),), "")
    return group


def _refresh_arc_metadata(cache_dir: Path, prn: str, arc_index: int) -> None:
    metadata_path = cache_dir / "metadata.json"
    if not metadata_path.exists():
        return
    metadata = _read_metadata(metadata_path)
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    group = zarr.open_group(str(path), mode="r")
    statuses = [_decode(x) for x in group["status"][:]]
    for entry in metadata.get("arc_entries", []):
        if entry.get("prn") == prn and int(entry.get("arc_index")) == int(arc_index):
            entry["stored_epoch_count"] = sum(1 for s in statuses if s == "ready")
            entry["failed_epoch_count"] = sum(1 for s in statuses if s in {"failed", "geometry_error", "insufficient_points"})
            entry["status"] = "complete" if entry["stored_epoch_count"] + entry["failed_epoch_count"] >= int(entry.get("expected_epoch_count", 0)) else "partial"
    metadata["updated_at"] = _now()
    _write_metadata(metadata_path, metadata)


def _stale(cache_dir: Path, metadata: dict[str, Any], reason: str) -> CacheValidation:
    metadata["stale"] = True
    metadata["stale_reason"] = reason
    metadata["updated_at"] = _now()
    _write_metadata(cache_dir / "metadata.json", metadata)
    return CacheValidation(False, reason, True, metadata)


def _read_metadata(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in payload.items()}
