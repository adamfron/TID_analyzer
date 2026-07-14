from math import acos, degrees, isclose

from tid_analyzer.solar import (
    EARTH_RADIUS_KM,
    IONOSPHERIC_SHELL_HEIGHT_KM,
    datetime_from_doy_decimal_hour,
    solar_geometry_for_epoch,
    solar_geometry_from_manifest,
)


def test_doy_conversion():
    assert datetime_from_doy_decimal_hour(2024, 246, 0).isoformat() == "2024-09-02T00:00:00+00:00"


def test_decimal_hour_conversion():
    assert datetime_from_doy_decimal_hour(2024, 246, 1.5).isoformat() == "2024-09-02T01:30:00+00:00"


def test_450_km_threshold_formula():
    grid = solar_geometry_from_manifest(2024, 246, 0)
    expected = -degrees(acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + IONOSPHERIC_SHELL_HEIGHT_KM)))
    assert isclose(grid.ionospheric_shadow["threshold_deg"], expected)
    assert grid.ionospheric_shadow["formula"] == "-degrees(acos(R / (R + h)))"


def test_automatic_update_between_epochs():
    first = solar_geometry_from_manifest(2024, 246, 0)
    second = solar_geometry_from_manifest(2024, 246, 1)
    assert first.utc_datetime != second.utc_datetime
    assert first.subsolar_longitude != second.subsolar_longitude


def test_contour_separation():
    thresholds = solar_geometry_from_manifest(2024, 246, 0).contour_thresholds_deg
    assert 0 in thresholds and -6 in thresholds and -12 in thresholds and -18 in thresholds
    assert len(set(thresholds)) == len(thresholds)


def test_cache_reuse():
    first = solar_geometry_for_epoch(1725235200)
    second = solar_geometry_for_epoch(1725235200)
    assert first is second
from pathlib import Path


def test_frontend_layer_ordering():
    source = Path("frontend/src/main.tsx").read_text()
    order = [
        'fill="#fff"',
        "<SolarShading",
        "className=\"dtecRaster\"",
        "<Borders",
        "<SolarContours",
        "className=\"stationRays\"",
        "layers.ipp",
        "className={`stationMarker",
        "className=\"mapFrame\"",
    ]
    positions = [source.index(token) for token in order]
    assert positions == sorted(positions)


def test_frontend_default_solar_layers_and_master_toggle():
    source = Path("frontend/src/main.tsx").read_text()
    assert "solar:true" in source
    assert "solarHorizon:true" in source
    assert "solarIono:true" in source
    assert "solarShade:true" in source
    assert "solarCivil:false" in source and "solarNautical:false" in source and "solarAstronomical:false" in source
    assert "Show solar illumination" in source
