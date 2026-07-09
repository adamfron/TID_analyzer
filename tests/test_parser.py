from pathlib import Path

from tid_analyzer.importer.parser import build_manifest, station_from_filename

FIXTURES = Path(__file__).parent / "fixtures"


def test_station_code_from_filename() -> None:
    assert station_from_filename(Path("LAMA_2024_246.txt")) == "LAMA"


def test_manifest_collects_filtered_metadata(tmp_path: Path) -> None:
    manifest = build_manifest(FIXTURES, tmp_path)

    assert manifest["year"] == 2024
    assert manifest["doy"] == 246
    assert manifest["station_count"] == 2
    assert manifest["stations"] == ["GWWL", "LAMA"]
    assert manifest["gps_prns"] == ["G05", "G12", "G24"]
    assert manifest["time_range_hours"] == {"min": 0.0, "max": 0.025}
    assert manifest["row_counts_by_station"] == {"GWWL": 2, "LAMA": 2}
    assert manifest["row_counts_by_prn"] == {"G05": 1, "G12": 1, "G24": 2}
    assert manifest["malformed_row_count"] == 1
    assert manifest["ipp_bounds"] == {"lon_min": 19.5, "lon_max": 23.0, "lat_min": 50.1, "lat_max": 52.4}
    assert (tmp_path / "day_manifest.json").exists()


def test_ignores_non_gps_rows(tmp_path: Path) -> None:
    manifest = build_manifest(FIXTURES, tmp_path)
    assert "E02" not in manifest["gps_prns"]
    assert "R01" not in manifest["gps_prns"]


def test_applies_minimum_elevation(tmp_path: Path) -> None:
    manifest = build_manifest(FIXTURES, tmp_path)
    assert manifest["row_counts_by_prn"]["G24"] == 2
    assert manifest["applied_filters"]["min_elevation_deg"] == 50.0
