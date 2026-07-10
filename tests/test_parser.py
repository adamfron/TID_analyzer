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


def test_preview_endpoint_snaps_current_epoch_to_nearest_available_time() -> None:
    state.source_folder = FIXTURES
    data = TestClient(app).get("/api/preview/points?prn=G24&time_h=0.02&tolerance_seconds=1").json()
    assert data["mode_used"] == "current_epoch"
    assert data["requested_time_h"] == 0.02
    assert data["actual_time_h"] == 0.0
    assert data["count_returned"] == 1
    assert data["station_markers"] == [{"station": "LAMA", "lon": 19.5, "lat": 50.1, "approximate": True, "source": "mean_epoch_ipp"}]
    assert data["raster_available"] is False
    assert data["interpolated_dtec"] is None


def test_world_borders_endpoint_returns_geojson() -> None:
    response = TestClient(app).get("/api/assets/world-borders")
    assert response.status_code == 200
    assert response.json()["type"] == "FeatureCollection"


def test_visibility_endpoint_returns_arcs() -> None:
    state.source_folder = FIXTURES
    data = TestClient(app).get("/api/satellites/visibility").json()
    assert data["arcs"]
    assert {arc["prn"] for arc in data["arcs"]} == {"G05", "G12", "G24"}


def test_visibility_endpoint_splits_multiple_arcs_by_time_gap(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text(
        "0.0;G24;1;1;80;10;50\n0.05;G24;1;1;80;10;50\n0.4;G24;1;1;80;10;50\n",
        encoding="utf-8",
    )
    state.source_folder = folder
    data = TestClient(app).get("/api/satellites/visibility?gap_minutes=10").json()
    arcs = [arc for arc in data["arcs"] if arc["prn"] == "G24"]
    assert len(arcs) == 2
    assert arcs[0]["row_count"] == 2
    assert arcs[1]["arc_index"] == 2


def test_preview_endpoint_treats_zero_time_as_valid(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text(
        "0.0;G24;1;1;80;10;50\n1.0;G24;2;1;80;10;50\n",
        encoding="utf-8",
    )
    state.source_folder = folder
    data = TestClient(app).get("/api/preview/points?prn=G24&time_h=0&tolerance_seconds=1").json()
    assert data["mode_used"] == "current_epoch"
    assert data["count_returned"] == 1
    assert data["points"][0]["time_h"] == 0.0


def test_preview_endpoint_supports_start_end_time_window(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text(
        "0.0;G24;1;1;80;10;50\n0.5;G24;2;1;80;10;50\n1.0;G24;3;1;80;10;50\n",
        encoding="utf-8",
    )
    state.source_folder = folder
    data = TestClient(app).get("/api/preview/points?prn=G24&start_time_h=0.25&end_time_h=0.75").json()
    assert data["mode_used"] == "selected_time_window"
    assert [p["time_h"] for p in data["points"]] == [0.5]


def test_preview_endpoint_uses_deterministic_sampling(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    rows = "".join(f"{i};G24;{i};1;80;10;50\n" for i in range(10))
    (folder / "TEST_2024_246.txt").write_text(rows, encoding="utf-8")
    state.source_folder = folder
    client = TestClient(app)
    first = client.get("/api/preview/points?prn=G24&max_points=3").json()
    second = client.get("/api/preview/points?prn=G24&max_points=3").json()
    assert first == second
    assert first["limit_reached"] is True
    assert [p["time_h"] for p in first["points"]] == [0.0, 4.0, 9.0]


def test_station_timeseries_endpoint_returns_selected_station_series() -> None:
    state.source_folder = FIXTURES
    data = TestClient(app).get("/api/stations/timeseries?station=LAMA&prn=G24").json()
    assert data["series"][0]["station"] == "LAMA"
    assert data["series"][0]["prn"] == "G24"
    assert [p["time_h"] for p in data["series"][0]["points"]] == [0.0]


def test_import_creates_duckdb_cache(tmp_path: Path) -> None:
    manifest = build_manifest(FIXTURES, tmp_path / "cache")
    cache_path = Path(str(manifest["cache_path"]))
    assert cache_path.exists()


def test_observations_table_has_expected_filtered_rows(tmp_path: Path) -> None:
    import duckdb
    manifest = build_manifest(FIXTURES, tmp_path / "cache")
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM observations WHERE prn NOT LIKE 'G%' OR elevation < 50").fetchone()[0] == 0


def test_epoch_index_is_30_second_rounding(tmp_path: Path) -> None:
    import duckdb
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0.0083333333;G24;1;1;80;10;50\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT epoch_index FROM observations").fetchone()[0] == 1


def test_map_epoch_endpoint_snaps_and_returns_points(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0.0;G24;1;1;80;10;50\n0.5;G24;2;1;80;11;51\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    state.source_folder = folder
    state.manifest = manifest
    state.cache_path = Path(str(manifest["cache_path"]))
    data = TestClient(app).get("/api/map/epoch?prn=G24&time_h=0.49").json()
    assert data["actual_time_h"] == 0.5
    assert data["count"] == 1
    assert data["stations"] == ["TEST"]
    assert data["points"][0]["dtec"] == 2.0
