from pathlib import Path

from fastapi.testclient import TestClient

from tid_analyzer.api.app import app, state
from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.parser import build_manifest, station_from_filename, iter_station_files

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
    try:
        build_manifest(folder, tmp_path / "cache")
    except RuntimeError as exc:
        assert "No valid observations passed" in str(exc)
        assert "malformed: 1" in str(exc)
    else:
        raise AssertionError("zero-valid malformed import should fail")


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
    assert data["station_markers"][0]["station"] == "LAMA"
    assert data["station_markers"][0]["approximate"] is False
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


def _spectral_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "spectral_day"; folder.mkdir()
    rows = []
    for i in range(64):
        t = i / 120  # 30-second cadence in hours.
        rows.append(f"{t:.10f};G24;{__import__('math').sin(i/4):.6f};120;70;20;50\n")
    (folder / "LAMA_2024_246.txt").write_text("".join(rows), encoding="utf-8")
    return folder


def test_fft_endpoint_returns_period_and_amplitude_arrays(tmp_path: Path) -> None:
    folder = _spectral_folder(tmp_path)
    manifest = build_manifest(folder, tmp_path / "cache")
    state.source_folder = folder
    state.manifest = manifest
    state.cache_path = Path(str(manifest["cache_path"]))
    data = TestClient(app).post("/api/spectral/fft", json={"station": "LAMA", "prn": "G24"}).json()
    assert data["station"] == "LAMA"
    assert data["prn"] == "G24"
    assert len(data["period_min"]) == len(data["amplitude"])
    assert data["period_min"]


def test_morlet_endpoint_returns_time_period_power_arrays(tmp_path: Path) -> None:
    folder = _spectral_folder(tmp_path)
    manifest = build_manifest(folder, tmp_path / "cache")
    state.source_folder = folder
    state.manifest = manifest
    state.cache_path = Path(str(manifest["cache_path"]))
    data = TestClient(app).post("/api/spectral/morlet", json={"station": "LAMA", "prn": "G24", "period_min_min": 2, "period_min_max": 30}).json()
    assert data["time_h"]
    assert data["period_min"]
    assert len(data["power"]) == len(data["period_min"])
    assert len(data["power"][0]) == len(data["time_h"])


def test_spectral_endpoint_reports_short_series(tmp_path: Path) -> None:
    folder = tmp_path / "short_day"; folder.mkdir()
    (folder / "LAMA_2024_246.txt").write_text("0;G24;1;120;70;20;50\n0.01;G24;2;120;70;20;50\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    state.source_folder = folder
    state.manifest = manifest
    state.cache_path = Path(str(manifest["cache_path"]))
    response = TestClient(app).post("/api/spectral/fft", json={"station": "LAMA", "prn": "G24"})
    assert response.status_code == 400
    assert "At least four observations" in response.json()["detail"]


def test_selected_elevation_is_applied_before_rows_are_stored(tmp_path: Path) -> None:
    import duckdb
    from tid_analyzer.config import ImportFilters
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0;G01;1;1;49;10;50\n0;G02;1;1;75;10;50\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache", ImportFilters(min_elevation_deg=70))
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert con.execute("SELECT MIN(elevation) FROM observations").fetchone()[0] >= 70


def test_progress_events_include_stage_fields(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0;G01;1;1;80;10;50\n", encoding="utf-8")
    events = []
    state.cache_dir = tmp_path / "cache"
    update = state._format_update("reading_filtering", 1, 2, "message")
    assert {"stage_index", "stage_count", "current", "total", "stage_percent", "overall_percent"} <= set(update)
    build_manifest(folder, tmp_path / "cache", progress=lambda *args: events.append(args))
    assert any(event[0] == "reading_filtering" and event[1] == 1 and event[2] == 1 for event in events)


def test_incomplete_cache_is_not_reused_but_completed_cache_is(tmp_path: Path) -> None:
    import duckdb
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0;G01;1;1;80;10;50\n", encoding="utf-8")
    first = build_manifest(folder, tmp_path / "cache")
    second = build_manifest(folder, tmp_path / "cache")
    assert second["cache_path"] == first["cache_path"]
    with duckdb.connect(str(first["cache_path"])) as con:
        con.execute("UPDATE metadata SET completed=false")
    (folder / "NEXT_2024_246.txt").write_text("0;G02;1;1;80;10;50\n", encoding="utf-8")
    rebuilt = build_manifest(folder, tmp_path / "cache")
    assert rebuilt["valid_rows_after_filters"] == 2


def test_cancel_marks_cache_incomplete(tmp_path: Path) -> None:
    import duckdb
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "TEST_2024_246.txt").write_text("0;G01;1;1;80;10;50\n", encoding="utf-8")
    try:
        build_manifest(folder, tmp_path / "cache", cancel=lambda: True)
    except RuntimeError as exc:
        assert "cancelled" in str(exc).lower()
    dbs = list((tmp_path / "cache").glob("**/tid_day.duckdb"))
    assert dbs
    with duckdb.connect(str(dbs[0]), read_only=True) as con:
        assert con.execute("SELECT completed FROM metadata").fetchone()[0] is False


def test_duckdb_import_normalizes_prn_whitespace_and_trailing_semicolon(tmp_path: Path) -> None:
    import duckdb
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "LAMA_2024_246.txt").write_text("0; G24 ;1;120;70;21;51;\n0;g12;1;120;70;22;52\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    assert manifest["valid_rows_after_filters"] == 2
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT list(prn ORDER BY prn) FROM observations").fetchone()[0] == ["G12", "G24"]


def test_zero_valid_import_reports_full_day_counts(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "LAMA_2024_246.txt").write_text("0; R24 ;1;120;70;21;51\n", encoding="utf-8")
    try:
        build_manifest(folder, tmp_path / "cache")
    except RuntimeError as exc:
        detail = str(exc)
        assert "No valid observations passed the full import filters" in detail
        assert "Parsed: 1" in detail
        assert "GPS: 0" in detail
    else:
        raise AssertionError("zero-valid import should fail")

def test_zero_valid_cache_is_not_reused(tmp_path: Path) -> None:
    import duckdb
    from tid_analyzer.importer.cache import cache_is_valid
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "LAMA_2024_246.txt").write_text("0;G01;1;120;80;21;51\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    db = Path(str(manifest["cache_path"]))
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE metadata SET completed=true, total_rows_seen=1, valid_rows_stored=0")
    assert not cache_is_valid(db, folder, iter_station_files(folder), 2024, 246, ImportFilters())


def test_station_code_extraction_and_catalog_matching(tmp_path: Path) -> None:
    from tid_analyzer.stations.catalog import resolve_stations, station_code_from_filename
    cache_dir = tmp_path / "cache" / "station_catalog"; cache_dir.mkdir(parents=True)
    cache_dir.joinpath("sample.SSC").write_text("LAMA00POL 3664940.500 1409153.600 5009571.100\n", encoding="utf-8")
    assert station_code_from_filename(Path("lama_2024_246.txt")) == "LAMA"
    rows = resolve_stations(["LAMA", "ODDNAME"], tmp_path / "cache", allow_download=False)
    lama = next(r for r in rows if r.station == "LAMA")
    odd = next(r for r in rows if r.station == "ODDNAME")
    assert lama.resolved and lama.full_site_id == "LAMA00POL"
    assert not odd.resolved


def test_exact_requested_row_field_mapping_and_filters() -> None:
    from tid_analyzer.importer.parser import parse_row

    row = parse_row("0.0; G28; 0.21664; -131.80717; 76.60443; -9.11857; 42.69704;", "TEST")
    assert row is not None
    assert row.elevation == 76.60443
    assert row.ipp_lon == -9.11857
    assert row.ipp_lat == 42.69704
    assert row.elevation >= 40
    assert row.elevation >= 50
    assert -20 <= row.ipp_lon <= 50
    assert 20 <= row.ipp_lat <= 80


def test_full_import_does_not_abort_before_later_high_elevation_row(tmp_path: Path) -> None:
    import duckdb

    folder = tmp_path / "day"; folder.mkdir()
    (folder / "EARLY_2024_246.txt").write_text("".join(f"{i};G01;1;1;30;10;50\n" for i in range(150)), encoding="utf-8")
    (folder / "LATE_2024_246.txt").write_text("0.0; G28; 0.21664; -131.80717; 76.60443; -9.11857; 42.69704;\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache", ImportFilters(min_elevation_deg=50))
    assert manifest["valid_rows_after_filters"] == 1
    assert manifest["low_elevation_row_count"] == 150
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT prn, elevation, ipp_lon, ipp_lat FROM observations").fetchone() == ("G28", 76.60443, -9.11857, 42.69704)


def test_file_with_single_valid_high_elevation_observation_is_stored(tmp_path: Path) -> None:
    import duckdb

    folder = tmp_path / "day"; folder.mkdir()
    (folder / "ONLY_2024_246.txt").write_text("0.0; G28; 0.21664; -131.80717; 76.60443; -9.11857; 42.69704;\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache", ImportFilters(min_elevation_deg=50))
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert con.execute("SELECT valid_row_count FROM imported_files WHERE filename='ONLY_2024_246.txt'").fetchone()[0] == 1


def test_python_fallback_and_duckdb_counter_parity(tmp_path: Path) -> None:
    import duckdb
    from tid_analyzer.importer.cache import configure_connection, create_schema, source_file_sql
    from tid_analyzer.importer.parser import _count_duckdb_file, _fallback_import_file, station_from_filename

    folder = tmp_path / "day"; folder.mkdir()
    path = folder / "PARI_2024_246.txt"
    path.write_text(
        "0;G01;1;1;80;10;50\n"
        "0;R01;1;1;80;10;50\n"
        "0;G02;1;1;30;10;50\n"
        "0;G03;1;1;80;99;50\n"
        "bad;G04;1;1;80;10;50\n",
        encoding="utf-8",
    )
    filters = ImportFilters(min_elevation_deg=50)
    with duckdb.connect(str(tmp_path / "duck.duckdb")) as con:
        configure_connection(con); create_schema(con)
        duck_counts = _count_duckdb_file(con, path, filters)
        con.execute(f"INSERT INTO observations SELECT * FROM ({source_file_sql(path, station_from_filename(path), filters)})")
        duck_counts.valid_rows_stored = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    with duckdb.connect(str(tmp_path / "py.duckdb")) as con:
        configure_connection(con); create_schema(con)
        py_counts = _fallback_import_file(con, path, filters)
    assert duck_counts.total_nonempty_rows == py_counts.total_nonempty_rows == 5
    assert duck_counts.parsed_rows == py_counts.parsed_rows == 4
    assert duck_counts.malformed_rows == py_counts.malformed_rows == 1
    assert duck_counts.non_gps_rows == py_counts.non_gps_rows == 1
    assert duck_counts.low_elevation_rows == py_counts.low_elevation_rows == 1
    assert duck_counts.out_of_bounds_rows == py_counts.out_of_bounds_rows == 1


def test_all_nonempty_rows_are_accounted_for(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "ACCT_2024_246.txt").write_text(
        "\n"
        "0;G01;1;1;80;10;50\n"
        "0;R01;1;1;80;10;50\n"
        "0;G02;1;1;30;10;50\n"
        "0;G03;1;1;80;99;50\n"
        "bad;G04;1;1;80;10;50\n",
        encoding="utf-8",
    )
    manifest = build_manifest(folder, tmp_path / "cache")
    diag = manifest["import_diagnostics"]
    assert diag["total_nonempty_rows"] == 5
    assert diag["malformed_rows"] + diag["non_gps_rows"] + diag["low_elevation_rows"] + diag["out_of_bounds_rows"] + diag["valid_rows_stored"] == diag["total_nonempty_rows"]


def test_acor_filename_matches_bundled_euref_station() -> None:
    from tid_analyzer.stations.catalog import resolve_stations, EUREF_SOURCE
    rows = resolve_stations(["ACOR"], Path(".tid_analyzer_cache"), allow_download=False)
    acor = rows[0]
    assert acor.full_site_id == "ACOR00ESP"
    assert acor.city == "A Coruna"
    assert acor.country == "ESP"
    assert acor.coordinate_source == EUREF_SOURCE
    assert acor.resolved


def test_bundled_csv_parsing_and_only_source_txt_stations_returned(tmp_path: Path) -> None:
    import duckdb
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "ACOR_2024_246.txt").write_text("0;G01;1;1;80;-8;43\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    with duckdb.connect(str(manifest["cache_path"]), read_only=True) as con:
        rows = con.execute("SELECT station, full_site_id, city, country FROM stations ORDER BY station").fetchall()
    assert rows == [("ACOR", "ACOR00ESP", "A Coruna", "ESP")]


def test_station_coordinates_remain_fixed_between_epochs_and_prn_filter(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "ACOR_2024_246.txt").write_text("0;G01;1;1;80;-1;40\n0.5;G01;1;1;80;20;60\n0.5;G02;1;1;80;30;70\n", encoding="utf-8")
    manifest = build_manifest(folder, tmp_path / "cache")
    state.source_folder = folder; state.manifest = manifest; state.cache_path = Path(str(manifest["cache_path"]))
    client = TestClient(app)
    first = client.get("/api/map/epoch?prn=G01&time_h=0").json()["station_markers"][0]
    second = client.get("/api/map/epoch?prn=G01&time_h=0.5").json()["station_markers"][0]
    g2 = client.get("/api/map/epoch?prn=G02&time_h=0.5").json()["station_markers"][0]
    assert (first["lon"], first["lat"]) == (second["lon"], second["lat"]) == (g2["lon"], g2["lat"])
    assert first["lon"] == -8.3989 and first["lat"] == 43.3644


def test_frontend_load_epoch_button_wraps_handler_and_guards_events() -> None:
    text = Path("frontend/src/main.tsx").read_text(encoding="utf-8")
    assert "onClick={()=>p.loadPreview()}" in text
    assert "typeof overrideTimeH === 'string' || typeof overrideTimeH === 'number'" in text
    assert "const safePrn = typeof overridePrn === 'string'" in text


def test_frontend_station_ipp_rays_and_plot_labels() -> None:
    text = Path("frontend/src/main.tsx").read_text(encoding="utf-8")
    assert "Show station–IPP rays" in text
    assert "Station–IPP ray" in text
    assert "x1={x(m.lon)}" in text and "x2={x(p.ipp_lon)}" in text
    assert "const minD=-1, maxD=1" in text
    assert "values outside displayed range [-1, 1] TECU" in text
    assert "Period [min]" in text and "Amplitude [TECU]" in text
    assert "Time [h UT]" in text and "Power" in text


def _arc_rows(station_sets: list[list[str]], start_h: float = 0.0, step_min: float = 30.0):
    return [
        ("G99", i, start_h + i * step_min / 60.0, len(stations), len(set(stations)), tuple(sorted(set(stations))))
        for i, stations in enumerate(station_sets)
    ]


def test_visibility_arc_interpolation_eligibility_threshold_examples() -> None:
    from tid_analyzer.api.app import _visibility_arc_from_epochs

    ninety_nine = [f"S{i:03d}" for i in range(99)]
    hundred = [f"S{i:03d}" for i in range(100)]

    arc = _visibility_arc_from_epochs("G99", 1, _arc_rows([ninety_nine] * 14, step_min=10.0))
    assert arc["duration_min"] == 130
    assert arc["eligible_for_interpolation"] is False
    assert arc["ineligibility_reasons"] == ["fewer than 100 stations"]

    arc = _visibility_arc_from_epochs("G99", 1, _arc_rows([hundred] * 240, step_min=119.5 / 239))
    assert arc["duration_min"] == 119.5
    assert arc["eligible_for_interpolation"] is False
    assert arc["ineligibility_reasons"] == ["duration shorter than 120 minutes"]

    arc = _visibility_arc_from_epochs("G99", 1, _arc_rows([hundred] * 13, step_min=10.0))
    assert arc["duration_min"] == 120
    assert arc["eligible_for_interpolation"] is True
    assert arc["ineligibility_reasons"] == []

    arc = _visibility_arc_from_epochs("G99", 1, _arc_rows([ninety_nine] * 10, step_min=10.0))
    assert arc["eligible_for_interpolation"] is False
    assert arc["ineligibility_reasons"] == ["fewer than 100 stations", "duration shorter than 120 minutes"]


def test_visibility_arc_station_epoch_and_interpolation_metadata_are_deterministic() -> None:
    from tid_analyzer.api.app import _visibility_arc_from_epochs

    rows = _arc_rows([
        ["A", "A", "B"],
        ["B", "C", "D", "E"],
        ["A", "E"],
    ])
    arc = _visibility_arc_from_epochs("G99", 1, rows)
    assert arc["station_count"] == 5
    assert arc["epoch_count"] == 3
    assert arc["max_station_count"] == 4
    assert arc["median_station_count"] == 2.0
    assert arc["interpolation_status"] == "not_generated"
    assert arc["generated_map_count"] == 0
    assert arc["failed_map_count"] == 0


def test_visibility_endpoint_station_count_uses_unique_codes(tmp_path: Path) -> None:
    folder = tmp_path / "day"; folder.mkdir()
    (folder / "DUPA_2024_246.txt").write_text("0.0;G24;1;1;80;10;50\n0.0;G24;2;1;80;10;50\n", encoding="utf-8")
    (folder / "DUPB_2024_246.txt").write_text("0.0;G24;1;1;80;10;50\n", encoding="utf-8")
    state.source_folder = folder
    state.cache_path = None
    state.manifest = None
    data = TestClient(app).get("/api/satellites/visibility?gap_minutes=10").json()
    arc = data["arcs"][0]
    assert arc["row_count"] == 3
    assert arc["station_count"] == 2
    assert arc["epoch_count"] == 1
    assert arc["median_station_count"] == 2.0


def test_visibility_frontend_keeps_ineligible_rows_selectable_and_no_interpolation_jobs() -> None:
    source = Path("frontend/src/main.tsx").read_text(encoding="utf-8")
    assert "className={a.eligible_for_interpolation ? '' : 'ineligibleArc'}" in source
    assert "onClick={()=>choose(a)}" in source
    assert "Planned maps" in source
    assert "interpolation/job" not in source.lower()
    assert "interpolation/files" not in source.lower()
