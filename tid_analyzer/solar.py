from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from math import acos, asin, atan2, cos, degrees, radians, sin

import numpy as np

LON_MIN, LON_MAX = -20.0, 50.0
LAT_MIN, LAT_MAX = 20.0, 80.0
GRID_STEP_DEG = 0.5
EARTH_RADIUS_KM = 6371.0
IONOSPHERIC_SHELL_HEIGHT_KM = 450.0
IONOSPHERIC_SHADOW_THRESHOLD_DEG = -degrees(acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + IONOSPHERIC_SHELL_HEIGHT_KM)))
CONTOUR_THRESHOLDS_DEG = [0.0, -6.0, -12.0, -18.0, IONOSPHERIC_SHADOW_THRESHOLD_DEG]


def datetime_from_doy_decimal_hour(year: int, doy: int, actual_time_h: float) -> datetime:
    """Convert manifest year, one-indexed DOY, and decimal UTC hour to datetime."""
    if doy < 1 or doy > 366:
        raise ValueError("day of year must be in 1..366")
    base = datetime(int(year), 1, 1, tzinfo=UTC) + timedelta(days=int(doy) - 1)
    return base + timedelta(hours=float(actual_time_h))


def _julian_day(dt: datetime) -> float:
    epoch = datetime(2000, 1, 1, 12, tzinfo=UTC)
    return 2451545.0 + (dt.astimezone(UTC) - epoch).total_seconds() / 86400.0


def _sun_ra_dec(dt: datetime) -> tuple[float, float]:
    n = _julian_day(dt) - 2451545.0
    mean_lon = radians((280.460 + 0.9856474 * n) % 360.0)
    mean_anom = radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_lon = mean_lon + radians(1.915) * sin(mean_anom) + radians(0.020) * sin(2 * mean_anom)
    obliquity = radians(23.439 - 0.0000004 * n)
    ra = atan2(cos(obliquity) * sin(ecliptic_lon), cos(ecliptic_lon))
    dec = asin(sin(obliquity) * sin(ecliptic_lon))
    return ra, dec


def _gmst(dt: datetime) -> float:
    jd = _julian_day(dt)
    t = (jd - 2451545.0) / 36525.0
    return radians((280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t * t * t / 38710000.0) % 360.0)


@dataclass(frozen=True)
class SolarGrid:
    utc_datetime: str
    subsolar_latitude: float
    subsolar_longitude: float
    lon_values: tuple[float, ...]
    lat_values: tuple[float, ...]
    solar_elevation_deg: tuple[tuple[float, ...], ...]
    contour_thresholds_deg: tuple[float, ...]
    ionospheric_shadow: dict[str, float]


@lru_cache(maxsize=256)
def solar_geometry_for_epoch(epoch_seconds: int) -> SolarGrid:
    dt = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    ra, dec = _sun_ra_dec(dt)
    sub_lon = ((degrees(ra - _gmst(dt)) + 180.0) % 360.0) - 180.0
    sub_lat = degrees(dec)
    lon_values = np.round(np.arange(LON_MIN, LON_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG), 6)
    lat_values = np.round(np.arange(LAT_MIN, LAT_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG), 6)
    lon_rad = np.radians(lon_values)[None, :]
    lat_rad = np.radians(lat_values)[:, None]
    dec_rad = radians(sub_lat)
    hour_angle = lon_rad - radians(sub_lon)
    elev = np.degrees(np.arcsin(np.sin(lat_rad) * sin(dec_rad) + np.cos(lat_rad) * cos(dec_rad) * np.cos(hour_angle)))
    return SolarGrid(
        utc_datetime=dt.isoformat().replace("+00:00", "Z"),
        subsolar_latitude=float(sub_lat),
        subsolar_longitude=float(sub_lon),
        lon_values=tuple(float(x) for x in lon_values),
        lat_values=tuple(float(x) for x in lat_values),
        solar_elevation_deg=tuple(tuple(float(v) for v in row) for row in elev),
        contour_thresholds_deg=tuple(CONTOUR_THRESHOLDS_DEG),
        ionospheric_shadow={"earth_radius_km": EARTH_RADIUS_KM, "shell_height_km": IONOSPHERIC_SHELL_HEIGHT_KM, "formula": "-degrees(acos(R / (R + h)))", "threshold_deg": IONOSPHERIC_SHADOW_THRESHOLD_DEG},
    )


def solar_geometry_from_manifest(year: int, doy: int, actual_time_h: float) -> SolarGrid:
    dt = datetime_from_doy_decimal_hour(year, doy, actual_time_h)
    return solar_geometry_for_epoch(round(dt.timestamp()))
