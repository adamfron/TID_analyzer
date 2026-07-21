"""Natural-neighbour interpolation for a single PRN/epoch.

This module intentionally contains only reusable scientific interpolation logic. It
performs no database access, batch orchestration, API work, or persistence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import numpy as np
try:
    from metpy.interpolate import natural_neighbor_to_grid
except ImportError:  # pragma: no cover - optional in minimal test environments
    natural_neighbor_to_grid = None
try:
    from pyproj import Transformer
except ImportError:  # pragma: no cover
    Transformer = None

METHOD = "metpy_natural_neighbor_liang_hale"
PROJECTION = "EPSG:3035"
SOURCE_CRS = "EPSG:4326"
LON_BOUNDS = (-20.0, 50.0)
LAT_BOUNDS = (20.0, 80.0)
DEFAULT_GRID_STEP_DEG = 0.5
STAGE_NAMES = ("database_query", "input_cleaning", "coordinate_projection", "duplicate_grouping", "triangulation_and_interpolation", "result_serialization", "zarr_write", "total_epoch_time")


@dataclass(frozen=True)
class GridGeometry:
    lon_values: np.ndarray
    lat_values: np.ndarray
    grid_lon: np.ndarray
    grid_lat: np.ndarray
    projected_grid_x: np.ndarray
    projected_grid_y: np.ndarray
    source_crs: str
    target_crs: str
    grid_step_deg: float


@dataclass(frozen=True)
class NaturalNeighborResult:
    prn: str | int
    epoch_index: int
    time_h: float
    method: str
    projection: str
    grid_step_deg: float
    lon_values: np.ndarray
    lat_values: np.ndarray
    values: np.ndarray
    valid_mask: np.ndarray
    point_count: int
    station_count: int
    status: str
    message: str
    input_row_count: int = 0
    output_finite_cell_count: int = 0
    timings: dict[str, float] = field(default_factory=dict)


def prepare_grid_geometry(grid_step_deg: float = DEFAULT_GRID_STEP_DEG, *, source_crs: str = SOURCE_CRS, target_crs: str = PROJECTION) -> GridGeometry:
    lon_values, lat_values, grid_lon, grid_lat = _target_grid(grid_step_deg)
    if Transformer is None:
        projected_grid_x = np.full(grid_lon.shape, np.nan, dtype=float)
        projected_grid_y = np.full(grid_lat.shape, np.nan, dtype=float)
    else:
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        projected_grid_x, projected_grid_y = transformer.transform(grid_lon, grid_lat)
    return GridGeometry(lon_values, lat_values, grid_lon, grid_lat, projected_grid_x, projected_grid_y, source_crs, target_crs, float(grid_step_deg))


def interpolate_prn_epoch_natural_neighbor(
    *, prn: str | int, epoch_index: int, time_h: float,
    station_codes: Sequence[Any] | np.ndarray | None = None, ipp_lon: Sequence[float] | np.ndarray | None = None,
    ipp_lat: Sequence[float] | np.ndarray | None = None, dtec: Sequence[float] | np.ndarray | None = None,
    rows: Iterable[Any] | None = None, grid_step_deg: float = DEFAULT_GRID_STEP_DEG, grid_geometry: GridGeometry | None = None,
) -> NaturalNeighborResult:
    """Interpolate dTEC for one PRN/epoch onto the fixed Europe lon/lat grid."""
    total_start = perf_counter(); timings = {name: 0.0 for name in STAGE_NAMES}
    geometry = grid_geometry or prepare_grid_geometry(grid_step_deg)
    empty_values = np.full(geometry.grid_lon.shape, np.nan, dtype=float)
    empty_mask = np.zeros(geometry.grid_lon.shape, dtype=bool)

    t = perf_counter()
    stations, lon, lat, values = _coerce_inputs(station_codes=station_codes, ipp_lon=ipp_lon, ipp_lat=ipp_lat, dtec=dtec, rows=rows)
    input_row_count = int(values.size)
    finite = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(values)
    in_bounds = (lon >= LON_BOUNDS[0]) & (lon <= LON_BOUNDS[1]) & (lat >= LAT_BOUNDS[0]) & (lat <= LAT_BOUNDS[1])
    keep = finite & in_bounds
    stations = stations[keep]; lon = lon[keep]; lat = lat[keep]; values = values[keep]
    station_count = len({str(station) for station in stations})
    timings["input_cleaning"] = perf_counter() - t

    if natural_neighbor_to_grid is None or Transformer is None:
        timings["total_epoch_time"] = perf_counter() - total_start
        return _result(prn, epoch_index, time_h, geometry, empty_values, empty_mask, 0, station_count, "failed", "MetPy and pyproj are required for natural-neighbour interpolation.", input_row_count, timings)

    t = perf_counter(); transformer = Transformer.from_crs(geometry.source_crs, geometry.target_crs, always_xy=True)
    if lon.size:
        point_x, point_y = transformer.transform(lon, lat)
    else:
        point_x = np.array([], dtype=float); point_y = np.array([], dtype=float)
    timings["coordinate_projection"] = perf_counter() - t

    t = perf_counter()
    if lon.size:
        point_x, point_y, values = _group_projected_points(point_x, point_y, values)
    else:
        values = np.array([], dtype=float)
    timings["duplicate_grouping"] = perf_counter() - t

    point_count = int(values.size)
    if point_count < 3:
        timings["total_epoch_time"] = perf_counter() - total_start
        return _result(prn, epoch_index, time_h, geometry, empty_values, empty_mask, point_count, station_count, "insufficient_points", "Fewer than 3 unique projected IPP positions remain after cleaning and grouping.", input_row_count, timings)

    t = perf_counter()
    try:
        interpolated = natural_neighbor_to_grid(point_x, point_y, values, geometry.projected_grid_x, geometry.projected_grid_y)
    except Exception as exc:  # MetPy/SciPy may raise several triangulation errors.
        timings["triangulation_and_interpolation"] = perf_counter() - t; timings["total_epoch_time"] = perf_counter() - total_start
        return _result(prn, epoch_index, time_h, geometry, empty_values, empty_mask, point_count, station_count, "geometry_error", f"Natural-neighbour interpolation failed for the projected geometry: {exc}", input_row_count, timings)
    timings["triangulation_and_interpolation"] = perf_counter() - t

    t = perf_counter(); interpolated = np.ma.filled(interpolated, np.nan).astype(float, copy=False); valid_mask = np.isfinite(interpolated); timings["result_serialization"] = perf_counter() - t
    timings["total_epoch_time"] = perf_counter() - total_start
    return _result(prn, epoch_index, time_h, geometry, interpolated, valid_mask, point_count, station_count, "ready", "Natural-neighbour interpolation completed.", input_row_count, timings)


def _result(prn, epoch_index, time_h, geometry, values, valid_mask, point_count, station_count, status, message, input_row_count, timings):
    return NaturalNeighborResult(prn=prn, epoch_index=epoch_index, time_h=time_h, method=METHOD, projection=geometry.target_crs, grid_step_deg=geometry.grid_step_deg, lon_values=geometry.lon_values, lat_values=geometry.lat_values, values=values, valid_mask=valid_mask, point_count=point_count, station_count=station_count, status=status, message=message, input_row_count=input_row_count, output_finite_cell_count=int(np.count_nonzero(valid_mask)), timings={k: float(v) for k, v in timings.items()})


def _target_grid(grid_step_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if grid_step_deg <= 0: raise ValueError("grid_step_deg must be positive")
    lon_values = np.arange(LON_BOUNDS[0], LON_BOUNDS[1] + grid_step_deg / 2.0, grid_step_deg)
    lat_values = np.arange(LAT_BOUNDS[0], LAT_BOUNDS[1] + grid_step_deg / 2.0, grid_step_deg)
    grid_lon, grid_lat = np.meshgrid(lon_values, lat_values)
    return lon_values, lat_values, grid_lon, grid_lat


def _coerce_inputs(*, station_codes, ipp_lon, ipp_lat, dtec, rows):
    if rows is not None:
        parsed = [_row_values(row) for row in rows]
        if parsed: station_codes, ipp_lon, ipp_lat, dtec = zip(*parsed, strict=True)
        else: station_codes, ipp_lon, ipp_lat, dtec = [], [], [], []
    elif any(arg is None for arg in (station_codes, ipp_lon, ipp_lat, dtec)):
        raise ValueError("Provide either rows or all four parallel input arrays")
    stations = np.asarray(station_codes, dtype=object); lon = np.asarray(ipp_lon, dtype=float); lat = np.asarray(ipp_lat, dtype=float); values = np.asarray(dtec, dtype=float)
    if len({stations.size, lon.size, lat.size, values.size}) != 1: raise ValueError("station_codes, ipp_lon, ipp_lat, and dtec must have the same length")
    return stations, lon, lat, values


def _row_values(row: Any) -> tuple[Any, float, float, float]:
    if isinstance(row, dict):
        station = row.get("station_code", row.get("station")); return station, row["ipp_lon"], row["ipp_lat"], row["dtec"]
    station = getattr(row, "station_code", getattr(row, "station", None)); return station, getattr(row, "ipp_lon"), getattr(row, "ipp_lat"), getattr(row, "dtec")


def _group_projected_points(point_x: np.ndarray, point_y: np.ndarray, values: np.ndarray):
    rounded_x = np.rint(point_x).astype(np.int64); rounded_y = np.rint(point_y).astype(np.int64); groups: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for x, y, rounded_key_x, rounded_key_y, value in zip(point_x, point_y, rounded_x, rounded_y, values, strict=True):
        groups.setdefault((int(rounded_key_x), int(rounded_key_y)), []).append((float(x), float(y), float(value)))
    grouped_x = []; grouped_y = []; grouped_values = []
    for key in sorted(groups):
        rows = np.asarray(groups[key], dtype=float); grouped_x.append(float(np.median(rows[:, 0]))); grouped_y.append(float(np.median(rows[:, 1]))); grouped_values.append(float(np.median(rows[:, 2])))
    return np.asarray(grouped_x), np.asarray(grouped_y), np.asarray(grouped_values)
