from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportFilters:
    constellation_prefix: str = "G"
    min_elevation_deg: float = 50.0
    lat_min: float = 20.0
    lat_max: float = 80.0
    lon_min: float = -20.0
    lon_max: float = 50.0
    ipp_height_km: float = 450.0
    epoch_step_seconds: int = 30
    grid_step_degrees: float = 0.5
    default_shapefile_name: str = "TM_WORLD_BORDERS-0.3.shp"

    def as_manifest_dict(self) -> dict[str, float | int | str]:
        return {
            "constellation_prefix": self.constellation_prefix,
            "min_elevation_deg": self.min_elevation_deg,
            "lat_min": self.lat_min,
            "lat_max": self.lat_max,
            "lon_min": self.lon_min,
            "lon_max": self.lon_max,
            "ipp_height_km": self.ipp_height_km,
            "epoch_step_seconds": self.epoch_step_seconds,
            "grid_step_degrees": self.grid_step_degrees,
            "default_shapefile_name": self.default_shapefile_name,
        }
