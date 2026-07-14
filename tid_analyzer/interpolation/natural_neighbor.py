"""Natural-neighbour interpolation for a single PRN/epoch.

This module intentionally contains only reusable scientific interpolation logic. It
performs no database access, batch orchestration, API work, or persistence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


def interpolate_prn_epoch_natural_neighbor(
    *,
    prn: str | int,
    epoch_index: int,
    time_h: float,
    station_codes: Sequence[Any] | np.ndarray | None = None,
    ipp_lon: Sequence[float] | np.ndarray | None = None,
    ipp_lat: Sequence[float] | np.ndarray | None = None,
    dtec: Sequence[float] | np.ndarray | None = None,
    rows: Iterable[Any] | None = None,
    grid_step_deg: float = DEFAULT_GRID_STEP_DEG,
) -> NaturalNeighborResult:
    """Interpolate dTEC for one PRN/epoch onto the fixed Europe lon/lat grid.

    Inputs may be supplied either as parallel arrays (``station_codes``,
    ``ipp_lon``, ``ipp_lat``, ``dtec``) or as ``rows`` with mapping keys or
    object attributes named ``station_code`` (or ``station``), ``ipp_lon``,
    ``ipp_lat``, and ``dtec``.
    """

    lon_values, lat_values, grid_lon, grid_lat = _target_grid(grid_step_deg)
    empty_values = np.full(grid_lon.shape, np.nan, dtype=float)
    empty_mask = np.zeros(grid_lon.shape, dtype=bool)

    stations, lon, lat, values = _coerce_inputs(
        station_codes=station_codes,
        ipp_lon=ipp_lon,
        ipp_lat=ipp_lat,
        dtec=dtec,
        rows=rows,
    )

    finite = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(values)
    in_bounds = (
        (lon >= LON_BOUNDS[0])
        & (lon <= LON_BOUNDS[1])
        & (lat >= LAT_BOUNDS[0])
        & (lat <= LAT_BOUNDS[1])
    )
    keep = finite & in_bounds
    stations = stations[keep]
    lon = lon[keep]
    lat = lat[keep]
    values = values[keep]

    station_count = len({str(station) for station in stations})

    if natural_neighbor_to_grid is None or Transformer is None:
        return NaturalNeighborResult(
            prn=prn,
            epoch_index=epoch_index,
            time_h=time_h,
            method=METHOD,
            projection=PROJECTION,
            grid_step_deg=grid_step_deg,
            lon_values=lon_values,
            lat_values=lat_values,
            values=empty_values,
            valid_mask=empty_mask,
            point_count=0,
            station_count=station_count,
            status="failed",
            message="MetPy and pyproj are required for natural-neighbour interpolation.",
        )

    transformer = Transformer.from_crs(SOURCE_CRS, PROJECTION, always_xy=True)
    target_x, target_y = transformer.transform(grid_lon, grid_lat)

    if lon.size:
        point_x, point_y = transformer.transform(lon, lat)
        point_x, point_y, values = _group_projected_points(point_x, point_y, values)
    else:
        point_x = np.array([], dtype=float)
        point_y = np.array([], dtype=float)
        values = np.array([], dtype=float)

    point_count = int(values.size)
    if point_count < 3:
        return NaturalNeighborResult(
            prn=prn,
            epoch_index=epoch_index,
            time_h=time_h,
            method=METHOD,
            projection=PROJECTION,
            grid_step_deg=grid_step_deg,
            lon_values=lon_values,
            lat_values=lat_values,
            values=empty_values,
            valid_mask=empty_mask,
            point_count=point_count,
            station_count=station_count,
            status="insufficient_points",
            message="Fewer than 3 unique projected IPP positions remain after cleaning and grouping.",
        )

    try:
        interpolated = natural_neighbor_to_grid(point_x, point_y, values, target_x, target_y)
    except Exception as exc:  # MetPy/SciPy may raise several triangulation errors.
        return NaturalNeighborResult(
            prn=prn,
            epoch_index=epoch_index,
            time_h=time_h,
            method=METHOD,
            projection=PROJECTION,
            grid_step_deg=grid_step_deg,
            lon_values=lon_values,
            lat_values=lat_values,
            values=empty_values,
            valid_mask=empty_mask,
            point_count=point_count,
            station_count=station_count,
            status="geometry_error",
            message=f"Natural-neighbour interpolation failed for the projected geometry: {exc}",
        )

    interpolated = np.ma.filled(interpolated, np.nan).astype(float, copy=False)
    valid_mask = np.isfinite(interpolated)

    return NaturalNeighborResult(
        prn=prn,
        epoch_index=epoch_index,
        time_h=time_h,
        method=METHOD,
        projection=PROJECTION,
        grid_step_deg=grid_step_deg,
        lon_values=lon_values,
        lat_values=lat_values,
        values=interpolated,
        valid_mask=valid_mask,
        point_count=point_count,
        station_count=station_count,
        status="ready",
        message="Natural-neighbour interpolation completed.",
    )


def _target_grid(grid_step_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if grid_step_deg <= 0:
        raise ValueError("grid_step_deg must be positive")
    lon_values = np.arange(LON_BOUNDS[0], LON_BOUNDS[1] + grid_step_deg / 2.0, grid_step_deg)
    lat_values = np.arange(LAT_BOUNDS[0], LAT_BOUNDS[1] + grid_step_deg / 2.0, grid_step_deg)
    grid_lon, grid_lat = np.meshgrid(lon_values, lat_values)
    return lon_values, lat_values, grid_lon, grid_lat


def _coerce_inputs(*, station_codes, ipp_lon, ipp_lat, dtec, rows):
    if rows is not None:
        parsed = [_row_values(row) for row in rows]
        if parsed:
            station_codes, ipp_lon, ipp_lat, dtec = zip(*parsed, strict=True)
        else:
            station_codes, ipp_lon, ipp_lat, dtec = [], [], [], []
    elif any(arg is None for arg in (station_codes, ipp_lon, ipp_lat, dtec)):
        raise ValueError("Provide either rows or all four parallel input arrays")

    stations = np.asarray(station_codes, dtype=object)
    lon = np.asarray(ipp_lon, dtype=float)
    lat = np.asarray(ipp_lat, dtype=float)
    values = np.asarray(dtec, dtype=float)
    lengths = {stations.size, lon.size, lat.size, values.size}
    if len(lengths) != 1:
        raise ValueError("station_codes, ipp_lon, ipp_lat, and dtec must have the same length")
    return stations, lon, lat, values


def _row_values(row: Any) -> tuple[Any, float, float, float]:
    if isinstance(row, dict):
        station = row.get("station_code", row.get("station"))
        return station, row["ipp_lon"], row["ipp_lat"], row["dtec"]
    station = getattr(row, "station_code", getattr(row, "station", None))
    return station, getattr(row, "ipp_lon"), getattr(row, "ipp_lat"), getattr(row, "dtec")


def _group_projected_points(point_x: np.ndarray, point_y: np.ndarray, values: np.ndarray):
    rounded_x = np.rint(point_x).astype(np.int64)
    rounded_y = np.rint(point_y).astype(np.int64)
    groups: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for x, y, rounded_key_x, rounded_key_y, value in zip(point_x, point_y, rounded_x, rounded_y, values, strict=True):
        groups.setdefault((int(rounded_key_x), int(rounded_key_y)), []).append((float(x), float(y), float(value)))

    grouped_x = []
    grouped_y = []
    grouped_values = []
    for key in sorted(groups):
        rows = np.asarray(groups[key], dtype=float)
        grouped_x.append(float(np.median(rows[:, 0])))
        grouped_y.append(float(np.median(rows[:, 1])))
        grouped_values.append(float(np.median(rows[:, 2])))

    return np.asarray(grouped_x), np.asarray(grouped_y), np.asarray(grouped_values)
