from pathlib import Path

from fastapi.testclient import TestClient

from tid_analyzer.api.app import app, state
from tid_analyzer.importer.parser import build_manifest, station_from_filename

FIXTURES = Path(__file__).parent / "fixtures"


def test_station_code_from_filename() -> None:
    assert station_from_filename(Path("LAMA_2024_246.txt")) == "LAMA"


def test_parser_accepts_trailing_semicolon_rows(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0.0; G24; -0.123; 120.0; 65.0; 20.1; 52.1;\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    assert manifest["valid_rows_after_filters"] == 1
    assert manifest["malformed_row_count"] == 0


def test_parser_accepts_no_trailing_semicolon_rows(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0.0;G24;-0.123;120.0;65.0;20.1;52.1\n", encoding="utf-8")
    assert build_manifest(folder, tmp_path / "cache")["valid_rows_after_filters"] == 1


def test_parser_rejects_truly_malformed_rows(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("malformed;G01;bad\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    assert manifest["malformed_row_count"] == 1
    assert manifest["valid_rows_after_filters"] == 0


def test_manifest_collects_filtered_metadata(tmp_path: Path) -> None:
    manifest = build_manifest(FIXTURES, tmp_path)
    assert manifest["year"] == 2024
    assert manifest["doy"] == 246
    assert manifest["station_count"] == 2
    assert manifest["stations"] == ["GWWL", "LAMA"]
    assert manifest["gps_prns"] == ["G05", "G12", "G24"]
    assert manifest["time_range_hours"] == {"min": 0.0, "max": 0.025}
    assert manifest["valid_rows_after_filters"] == 4
    assert manifest["total_rows_seen"] == 8
    assert manifest["malformed_row_count"] == 1
    assert manifest["non_gps_row_count"] == 2
    assert manifest["low_elevation_row_count"] == 1
    assert manifest["out_of_bounds_row_count"] == 0
    assert manifest["ipp_bounds"] == {"lon_min": 19.5, "lon_max": 23.0, "lat_min": 50.1, "lat_max": 52.4}


def test_parser_counts_filter_rejections_separately(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0;E01;1;1;80;10;50\n0;G01;1;1;49;10;50\n0;G02;1;1;80;99;50\n0;G03;1;1;80;10;50\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    assert manifest["non_gps_row_count"] == 1
    assert manifest["low_elevation_row_count"] == 1
    assert manifest["out_of_bounds_row_count"] == 1
    assert manifest["valid_rows_after_filters"] == 1


def test_preview_endpoint_returns_points_after_import_and_filters() -> None:
    state.source_folder = FIXTURES
    client = TestClient(app)
    data = client.get("/api/preview/points").json()
    assert data["count_returned"] == 4
    assert data["points"][0]["prn"].startswith("G")


def test_preview_endpoint_supports_prn_filter() -> None:
    state.source_folder = FIXTURES
    data = TestClient(app).get("/api/preview/points?prn=G12").json()
    assert data["count_returned"] == 1
    assert data["points"][0]["prn"] == "G12"


def test_preview_endpoint_supports_time_h_tolerance() -> None:
    state.source_folder = FIXTURES
    data = TestClient(app).get("/api/preview/points?time_h=0.0&tolerance_seconds=1").json()
    assert data["count_returned"] == 2
