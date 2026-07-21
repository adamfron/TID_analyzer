from __future__ import annotations

from typing import Any
import asyncio
from statistics import median

import numpy as np
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
try:
    import pywt
except ImportError:  # pragma: no cover - dependency is declared for runtime installs.
    pywt = None

from tid_analyzer.config import DEFAULT_MINIMUM_EPOCH_IPP_COUNT, ImportFilters
from tid_analyzer.api.state import ImportState
from tid_analyzer.interpolation.orchestrator import InterpolationController, eligible_arcs_from_daily, get_epoch_status
from tid_analyzer.interpolation.storage import read_epoch_result, validate_interpolation_cache
from tid_analyzer.interpolation.natural_neighbor import METHOD, PROJECTION, DEFAULT_GRID_STEP_DEG, prepare_grid_geometry, validate_grid_step
from tid_analyzer.importer.cache import connect_cache
from tid_analyzer.importer.parser import StationRow, build_manifest, iter_station_files, iter_valid_rows
from tid_analyzer.solar import solar_geometry_from_manifest

app = FastAPI(title="TID Analyzer API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
state = ImportState()
interpolation_state = InterpolationController(cache_dir=state.cache_dir)


class ImportRequest(BaseModel):
    folder_path: str
    min_elevation_deg: float = 50.0


class InterpolationBuildRequest(BaseModel):
    retry_failed: bool = False
    force_rebuild: bool = False
    minimum_epoch_ipp_count: int = DEFAULT_MINIMUM_EPOCH_IPP_COUNT
    grid_step_deg: float = DEFAULT_GRID_STEP_DEG

    @field_validator("grid_step_deg")
    @classmethod
    def _valid_grid_step(cls, value: float) -> float:
        return validate_grid_step(value)


class InterpolationBuildArcRequest(InterpolationBuildRequest):
    prn: str
    arc_index: int


class SpectralRequest(BaseModel):
    station: str
    prn: str
    start_time_h: float | None = None
    end_time_h: float | None = None
    period_min_min: float = 2
    period_min_max: float = 180



def _validated_grid_step_or_422(grid_step_deg: float) -> float:
    try:
        return validate_grid_step(grid_step_deg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

def _require_source_folder() -> Path:
    if state.source_folder is None:
        raise HTTPException(status_code=404, detail="No folder has been imported yet.")
    return state.source_folder


def _require_cache_path() -> Path:
    if state.cache_path is not None and state.cache_path.exists():
        return state.cache_path
    if state.manifest and state.manifest.get("cache_path"):
        path = Path(str(state.manifest["cache_path"]))
        if path.exists():
            state.cache_path = path
            return path
    if state.source_folder is not None:
        manifest = build_manifest(state.source_folder, state.cache_dir, ImportFilters())
        state.manifest = manifest
        path = Path(str(manifest["cache_path"]))
        state.cache_path = path
        return path
    raise HTTPException(status_code=404, detail="No DuckDB cache is available yet. Start an import first.")


def _row_from_tuple(values: tuple[object, ...]) -> StationRow:
    return StationRow(station=str(values[0]), prn=str(values[1]), time_h=float(values[2]), dtec=float(values[4]), azimuth=float(values[5]), elevation=float(values[6]), ipp_lon=float(values[7]), ipp_lat=float(values[8]))


def _iter_import_rows() -> list[StationRow]:
    folder = _require_source_folder()
    rows: list[StationRow] = []
    filters = ImportFilters()
    for path in iter_station_files(folder):
        rows.extend(iter_valid_rows(path, filters))
    return rows


def _deterministic_sample(rows: list[StationRow], max_points: int) -> tuple[list[StationRow], bool]:
    if len(rows) <= max_points:
        return rows, False
    if max_points == 1:
        return [rows[0]], True
    last = len(rows) - 1
    indices = sorted({round(i * last / (max_points - 1)) for i in range(max_points)})
    sampled = [rows[i] for i in indices]
    return sampled, True


@app.post("/api/import")
async def start_import(request: ImportRequest, force_rebuild: bool = False) -> dict[str, str]:
    if request.min_elevation_deg < 0 or request.min_elevation_deg > 90:
        raise HTTPException(status_code=400, detail="min_elevation_deg must be between 0 and 90.")
    try:
        await state.start_import(Path(request.folder_path).expanduser(), ImportFilters(min_elevation_deg=request.min_elevation_deg), force_rebuild)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Import started"}


@app.post("/api/import/cancel")
async def cancel_import() -> dict[str, str]:
    await state.cancel_import()
    return {"message": "Import cancellation requested"}


@app.get("/api/import/status")
async def get_status() -> dict[str, object]:
    return state.status


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "import_state": str(state.status.get("stage", "idle")), "interpolation_state": str(interpolation_state.status.get("state", "idle"))}


@app.get("/api/manifest")
async def get_manifest() -> dict[str, object]:
    if state.manifest is None:
        raise HTTPException(status_code=404, detail="No manifest is available yet. Start an import first.")
    return state.manifest




@app.get("/api/solar-geometry")
async def solar_geometry(actual_time_h: float) -> dict[str, object]:
    if state.manifest is None:
        raise HTTPException(status_code=404, detail="No manifest is available yet. Start an import first.")
    try:
        year = int(state.manifest["year"])
        doy = int(state.manifest["doy"])
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="Imported manifest does not include year/day-of-year metadata.") from exc
    grid = solar_geometry_from_manifest(year, doy, actual_time_h)
    return {
        "utc_datetime": grid.utc_datetime,
        "subsolar_latitude": grid.subsolar_latitude,
        "subsolar_longitude": grid.subsolar_longitude,
        "lon_values": list(grid.lon_values),
        "lat_values": list(grid.lat_values),
        "solar_elevation_deg": [list(row) for row in grid.solar_elevation_deg],
        "contour_thresholds_deg": list(grid.contour_thresholds_deg),
        "ionospheric_shadow": grid.ionospheric_shadow,
    }

@app.get("/api/stations/catalog")
async def station_catalog() -> dict[str, object]:
    cache_path = _require_cache_path()
    with connect_cache(cache_path) as con:
        try:
            rows = con.execute("SELECT station, full_site_id, city, country, domes, longitude, latitude, height, coordinate_source, resolved, resolution_note FROM stations ORDER BY station").fetchall()
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Station catalogue is not available yet.") from exc
    stations = [{"station": r[0], "full_site_id": r[1], "city": r[2], "country": r[3], "domes": r[4], "lon": r[5], "lat": r[6], "height": r[7], "source": r[8], "resolved": bool(r[9]), "resolution_note": r[10]} for r in rows]
    resolved = sum(1 for s in stations if s["resolved"])
    return {"total": len(stations), "resolved": resolved, "unresolved": len(stations) - resolved, "stations": stations}

@app.get("/api/assets/world-borders")
async def world_borders() -> Response:
    repo_asset = Path(__file__).resolve().parents[2] / "assets/world/TM_WORLD_BORDERS-0.3.geojson"
    try:
        text = repo_asset.read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            asset = resources.files("tid_analyzer").joinpath("assets/world/TM_WORLD_BORDERS-0.3.geojson")
            text = asset.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="World borders GeoJSON asset is missing from assets/world or tid_analyzer/assets/world.") from exc
        except ModuleNotFoundError as exc:
            raise HTTPException(status_code=404, detail="World borders asset package is unavailable.") from exc
    return Response(content=text, media_type="application/geo+json")


@app.get("/api/preview/points")
async def preview_points(
    prn: str | None = None,
    time_h: float | None = None,
    start_time_h: float | None = None,
    end_time_h: float | None = None,
    tolerance_seconds: float = 15,
    max_points: int = Query(5000, ge=1, le=100000),
) -> dict[str, object]:
    if start_time_h is not None and end_time_h is not None and start_time_h > end_time_h:
        raise HTTPException(status_code=400, detail="start_time_h must be <= end_time_h.")
    cache_path = _require_cache_path()
    params: list[object] = []
    mode = "whole_selected_satellite"
    actual_time_h: float | None = None
    where: list[str] = []
    if prn:
        where.append("prn = ?")
        params.append(prn)
    if time_h is not None:
        mode = "current_epoch"
        with connect_cache(cache_path) as con:
            row = con.execute("SELECT epoch_index, time_h FROM prn_epochs WHERE (? IS NULL OR prn = ?) ORDER BY ABS(time_h - ?), time_h LIMIT 1", [prn, prn, time_h]).fetchone()
        if row is not None:
            epoch_index = int(row[0])
            actual_time_h = float(row[1])
            where.append("epoch_index = ?")
            params.append(epoch_index)
    elif start_time_h is not None or end_time_h is not None:
        mode = "selected_time_window"
        if start_time_h is not None:
            where.append("time_h >= ?")
            params.append(start_time_h)
        if end_time_h is not None:
            where.append("time_h <= ?")
            params.append(end_time_h)
    sql = "SELECT station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat FROM observations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY time_h, prn, station"
    with connect_cache(cache_path) as con:
        tuples = con.execute(sql, params).fetchall()
    matching = [_row_from_tuple(t) for t in tuples]
    sampled, limit_reached = _deterministic_sample(matching, max_points)
    station_markers = _catalog_station_markers(cache_path, sorted({r.station for r in matching})) if mode == "current_epoch" else _catalog_station_markers(cache_path, None)
    return {"points": [row.__dict__ for row in sampled], "station_markers": station_markers, "interpolated_dtec": None, "raster_available": False, "requested_time_h": time_h, "actual_time_h": actual_time_h, "count_returned": len(sampled), "total_matching_before_limit": len(matching), "limit_reached": limit_reached, "mode_used": mode}


@app.get("/api/satellites/visibility")
async def satellite_visibility(gap_minutes: float = Query(10, gt=0), minimum_epoch_ipp_count: int = Query(DEFAULT_MINIMUM_EPOCH_IPP_COUNT, ge=3, le=200)) -> dict[str, object]:
    cache_path = _require_cache_path()
    gap_epochs = gap_minutes * 60 / 30
    arcs: list[dict[str, object]] = []
    with connect_cache(cache_path) as con:
        rows = con.execute("""
            SELECT prn, epoch_index, AVG(time_h) AS time_h, COUNT(*) AS row_count,
                   COUNT(DISTINCT station) AS station_count, LIST(DISTINCT station ORDER BY station) AS stations
            FROM observations
            GROUP BY prn, epoch_index
            ORDER BY prn, epoch_index
        """).fetchall()
    current: list[tuple[str, int, float, int, int, tuple[str, ...]]] = []
    arc_index = 1
    previous_prn: str | None = None
    previous_epoch: int | None = None
    for prn, epoch_index, time_h, row_count, station_count, stations in rows:
        prn = str(prn); epoch_index = int(epoch_index)
        starts_new_prn = previous_prn is not None and prn != previous_prn
        gap_split = previous_epoch is not None and epoch_index - previous_epoch > gap_epochs
        if current and (starts_new_prn or gap_split):
            arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current, minimum_epoch_ipp_count))
            arc_index = 1 if starts_new_prn else arc_index + 1
            current = []
        current.append((prn, epoch_index, float(time_h), int(row_count), int(station_count), tuple(str(s) for s in (stations or []))))
        previous_prn = prn
        previous_epoch = epoch_index
    if current:
        arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current, minimum_epoch_ipp_count))
    return {"arcs": _attach_interpolation_arc_status(cache_path, arcs)}


def _catalog_station_markers(cache_path: Path, station_codes: list[str] | None) -> list[dict[str, object]]:
    params: list[object] = []
    where = "resolved AND longitude IS NOT NULL AND latitude IS NOT NULL"
    if station_codes is not None:
        codes = sorted({code.upper() for code in station_codes})
        if not codes:
            return []
        where += " AND UPPER(station) IN (" + ",".join("?" for _ in codes) + ")"
        params.extend(codes)
    with connect_cache(cache_path) as con:
        rows = con.execute(f"""
            SELECT station, full_site_id, city, country, domes, longitude, latitude, height, coordinate_source, resolution_note
            FROM stations WHERE {where} ORDER BY station
        """, params).fetchall()
    return [{
        "station": r[0], "full_site_id": r[1], "city": r[2], "country": r[3], "domes": r[4],
        "lon": r[5], "lat": r[6], "height": r[7], "source": r[8], "resolved": True,
        "resolution_note": r[9], "approximate": False,
    } for r in rows]

def _attach_interpolation_arc_status(cache_path: Path, arcs: list[dict[str, object]]) -> list[dict[str, object]]:
    try:
        from tid_analyzer.interpolation.orchestrator import _cache_dir_for_daily
        cache_dir = _cache_dir_for_daily(state.cache_dir, cache_path, DEFAULT_GRID_STEP_DEG)
        metadata = validate_interpolation_cache(cache_dir).metadata or {}
        entries = {(e["prn"], int(e["arc_index"])): e for e in metadata.get("arc_entries", [])}
    except Exception:
        entries = {}
    out = []
    for arc in arcs:
        copied = dict(arc)
        if not copied.get("eligible_for_interpolation"):
            copied.update({"interpolation_status": "ineligible", "generated_map_count": 0, "failed_map_count": 0})
        else:
            entry = entries.get((str(copied["prn"]), int(copied["arc_index"])))
            ready = int((entry or {}).get("stored_epoch_count", 0)); failed = int((entry or {}).get("failed_epoch_count", 0)); expected = int(copied.get("usable_epoch_count", copied.get("epoch_count", 0)))
            status = "not_generated" if not entry or (ready == 0 and failed == 0) else ("ready" if ready >= expected and failed == 0 else ("failed" if ready == 0 and failed > 0 else "partial"))
            copied.update({"interpolation_status": status, "generated_map_count": ready, "failed_map_count": failed})
        out.append(copied)
    return out


def _visibility_arc_from_epochs(prn: str, arc_index: int, rows: list[tuple[str, int, float, int, int, tuple[str, ...]]], minimum_epoch_ipp_count: int = DEFAULT_MINIMUM_EPOCH_IPP_COUNT) -> dict[str, object]:
    start = rows[0][2]
    end = rows[-1][2]
    duration_min = (end - start) * 60
    per_epoch_station_counts = [int(r[4]) for r in rows]
    usable_rows = [r for r in rows if int(r[3]) >= minimum_epoch_ipp_count]
    low_coverage_epoch_count = len(rows) - len(usable_rows)
    unique_stations = {station for row in rows for station in row[5]}
    station_count = len(unique_stations)
    reasons: list[str] = []
    if station_count < 100:
        reasons.append("fewer than 100 stations")
    if duration_min < 120:
        reasons.append("duration shorter than 120 minutes")
    return {
        "prn": prn,
        "arc_index": arc_index,
        "start_time_h": start,
        "end_time_h": end,
        "duration_min": duration_min,
        "row_count": sum(r[3] for r in rows),
        "station_count": station_count,
        "epoch_count": len(rows),
        "total_epoch_count": len(rows),
        "usable_epoch_count": len(usable_rows),
        "raw_epoch_count": len(rows),
        "planned_interpolation_count": len(usable_rows),
        "low_point_epoch_count": low_coverage_epoch_count,
        "ready_map_count": 0,
        "minimum_epoch_ipp_count": int(minimum_epoch_ipp_count),
        "low_coverage_epoch_count": low_coverage_epoch_count,
        "first_usable_time_h": usable_rows[0][2] if usable_rows else None,
        "last_usable_time_h": usable_rows[-1][2] if usable_rows else None,
        "no_usable_epoch_reason": "No epoch meets the minimum IPP threshold; no interpolated map is planned." if not usable_rows else None,
        "max_station_count": max(per_epoch_station_counts),
        "median_station_count": float(median(per_epoch_station_counts)),
        "eligible_for_interpolation": not reasons,
        "ineligibility_reasons": reasons,
        "interpolation_status": "not_generated",
        "generated_map_count": 0,
        "failed_map_count": 0,
    }


@app.get("/api/map/epoch")
async def map_epoch(prn: str = Query(...), time_h: float = Query(...)) -> dict[str, object]:
    cache_path = _require_cache_path()
    with connect_cache(cache_path) as con:
        epoch = con.execute("SELECT epoch_index, time_h FROM prn_epochs WHERE prn = ? ORDER BY ABS(time_h - ?), time_h LIMIT 1", [prn, time_h]).fetchone()
        if epoch is None:
            raise HTTPException(status_code=404, detail=f"No epochs are available for PRN {prn}.")
        epoch_index, actual_time_h = int(epoch[0]), float(epoch[1])
        rows = con.execute("SELECT station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat FROM observations WHERE prn = ? AND epoch_index = ? ORDER BY station", [prn, epoch_index]).fetchall()
    points = [_row_from_tuple(t).__dict__ for t in rows]
    return {"prn": prn, "requested_time_h": time_h, "actual_time_h": actual_time_h, "epoch_index": epoch_index, "points": points, "count": len(points), "stations": sorted({str(p["station"]) for p in points}), "station_markers": _catalog_station_markers(cache_path, sorted({str(p["station"]) for p in points}))}


def _station_query_values(station: list[str]) -> list[str]:
    return sorted({part.strip().upper() for value in station for part in value.split(",") if part.strip()})


def _rows_for_station_prn(stations: list[str], prn: str, start_time_h: float | None = None, end_time_h: float | None = None) -> dict[str, list[StationRow]]:
    if not stations:
        raise HTTPException(status_code=400, detail="At least one station is required.")
    if start_time_h is not None and end_time_h is not None and start_time_h > end_time_h:
        raise HTTPException(status_code=400, detail="start_time_h must be <= end_time_h.")
    clauses = ["UPPER(station) IN (" + ",".join("?" for _ in stations) + ")", "prn = ?"]
    params: list[object] = [*stations, prn]
    if start_time_h is not None:
        clauses.append("time_h >= ?")
        params.append(start_time_h)
    if end_time_h is not None:
        clauses.append("time_h <= ?")
        params.append(end_time_h)
    sql = "SELECT station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat FROM observations WHERE " + " AND ".join(clauses) + " ORDER BY station, time_h"
    grouped: dict[str, list[StationRow]] = {name: [] for name in stations}
    with connect_cache(_require_cache_path()) as con:
        for tup in con.execute(sql, params).fetchall():
            row = _row_from_tuple(tup)
            grouped[row.station.upper()].append(row)
    return grouped


def _series_payload(name: str, prn: str, rows: list[StationRow], max_points: int) -> dict[str, Any]:
    sampled, limit_reached = _deterministic_sample(rows, max_points)
    return {"station": name, "prn": prn, "time_start_h": rows[0].time_h if rows else None, "time_end_h": rows[-1].time_h if rows else None, "points": [{"time_h": r.time_h, "dtec": r.dtec, "elevation": r.elevation, "ipp_lon": r.ipp_lon, "ipp_lat": r.ipp_lat} for r in sampled], "total_matching_before_limit": len(rows), "limit_reached": limit_reached}


@app.get("/api/stations/timeseries")
async def station_timeseries(
    station: list[str] = Query(...),
    prn: str = Query(...),
    start_time_h: float | None = None,
    end_time_h: float | None = None,
    arc_mode: str = "continuous_arc",
    max_points_per_series: int = Query(5000, ge=1, le=100000),
) -> dict[str, object]:
    if arc_mode != "continuous_arc":
        raise HTTPException(status_code=400, detail="Only arc_mode=continuous_arc is currently supported.")
    grouped = _rows_for_station_prn(_station_query_values(station), prn, start_time_h, end_time_h)
    return {"series": [_series_payload(name, prn, grouped[name], max_points_per_series) for name in sorted(grouped)]}


def _regularized_signal(request: SpectralRequest) -> tuple[np.ndarray, np.ndarray]:
    grouped = _rows_for_station_prn([request.station.upper()], request.prn, request.start_time_h, request.end_time_h)
    rows = grouped.get(request.station.upper(), [])
    if len(rows) < 4:
        raise HTTPException(status_code=400, detail="At least four observations are required for spectral analysis.")
    t = np.array([r.time_h for r in rows], dtype=float)
    y = np.array([r.dtec for r in rows], dtype=float)
    order = np.argsort(t)
    t, y = t[order], y[order]
    uniq, idx = np.unique(t, return_index=True)
    t, y = uniq, y[idx]
    if len(t) < 4 or t[-1] <= t[0]:
        raise HTTPException(status_code=400, detail="Time series is too short for spectral analysis.")
    step_h = 30 / 3600
    regular_t = np.arange(t[0], t[-1] + step_h / 2, step_h)
    if len(regular_t) < 4:
        raise HTTPException(status_code=400, detail="Time span is too short for spectral analysis.")
    regular_y = np.interp(regular_t, t, y)
    coeff = np.polyfit(regular_t - regular_t[0], regular_y, 1)
    detrended = regular_y - np.polyval(coeff, regular_t - regular_t[0])
    return regular_t, detrended


@app.post("/api/spectral/fft")
async def spectral_fft(request: SpectralRequest) -> dict[str, object]:
    t, y = _regularized_signal(request)
    dt_min = float(np.median(np.diff(t)) * 60)
    freqs = np.fft.rfftfreq(len(y), d=dt_min)
    amps = np.abs(np.fft.rfft(y)) * 2 / len(y)
    mask = freqs > 0
    periods = 1 / freqs[mask]
    amps = amps[mask]
    mask = (periods >= 2) & (periods <= 180)
    return {"station": request.station.upper(), "prn": request.prn, "period_min": periods[mask].tolist(), "amplitude": amps[mask].tolist()}


@app.post("/api/spectral/morlet")
async def spectral_morlet(request: SpectralRequest) -> dict[str, object]:
    if pywt is None:
        raise HTTPException(status_code=500, detail="PyWavelets is required for Morlet analysis.")
    if request.period_min_min <= 0 or request.period_min_max <= request.period_min_min:
        raise HTTPException(status_code=400, detail="Invalid Morlet period range.")
    t, y = _regularized_signal(request)
    dt_min = float(np.median(np.diff(t)) * 60)
    periods = np.linspace(request.period_min_min, request.period_min_max, 80)
    wavelet = pywt.ContinuousWavelet("cmor1.5-1.0")
    scales = pywt.frequency2scale(wavelet, 1 / (periods / dt_min))
    coeffs, _ = pywt.cwt(y, scales, wavelet, sampling_period=dt_min)
    return {"station": request.station.upper(), "prn": request.prn, "time_h": t.tolist(), "period_min": periods.tolist(), "power": (np.abs(coeffs) ** 2).tolist()}



@app.post("/api/interpolation/build-all")
async def interpolation_build_all(request: InterpolationBuildRequest) -> dict[str, object]:
    cache_path = _require_cache_path()
    try:
        response = await interpolation_state.start_build(daily_cache_path=cache_path, retry_failed=request.retry_failed, force_rebuild=request.force_rebuild, minimum_epoch_ipp_count=request.minimum_epoch_ipp_count, grid_step_deg=request.grid_step_deg)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Interpolation build started", **response}


@app.post("/api/interpolation/build-arc")
async def interpolation_build_arc(request: InterpolationBuildArcRequest) -> dict[str, object]:
    cache_path = _require_cache_path()
    try:
        response = await interpolation_state.start_build(daily_cache_path=cache_path, retry_failed=request.retry_failed, force_rebuild=request.force_rebuild, prn=request.prn, arc_index=request.arc_index, minimum_epoch_ipp_count=request.minimum_epoch_ipp_count, grid_step_deg=request.grid_step_deg)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Arc interpolation build started", **response}


@app.post("/api/interpolation/cancel")
async def interpolation_cancel() -> dict[str, str]:
    await interpolation_state.cancel()
    return {"message": "Interpolation cancellation requested"}


@app.get("/api/interpolation/status")
async def interpolation_status() -> dict[str, object]:
    return interpolation_state.status


@app.get("/api/interpolation/summary")
async def interpolation_summary(minimum_epoch_ipp_count: int = Query(DEFAULT_MINIMUM_EPOCH_IPP_COUNT, ge=3, le=200), grid_step_deg: float = Query(DEFAULT_GRID_STEP_DEG)) -> dict[str, object]:
    grid_step_deg = _validated_grid_step_or_422(grid_step_deg)
    cache_path = _require_cache_path()
    arcs = eligible_arcs_from_daily(cache_path, minimum_epoch_ipp_count=minimum_epoch_ipp_count)
    eligible = [a for a in arcs if a.get("eligible_for_interpolation")]
    try:
        from tid_analyzer.interpolation.orchestrator import _cache_dir_for_daily
        cache_dir = _cache_dir_for_daily(state.cache_dir, cache_path, grid_step_deg)
        validation = validate_interpolation_cache(cache_dir)
        metadata = validation.metadata or {}
    except Exception:
        validation = None; metadata = {}
    entries = {(e["prn"], int(e["arc_index"])): e for e in metadata.get("arc_entries", [])}
    per_arc=[]; ready=failed=0
    for arc in eligible:
        e = entries.get((str(arc["prn"]), int(arc["arc_index"])), {})
        r=int(e.get("stored_epoch_count",0)); f=int(e.get("failed_epoch_count",0)); exp=int(arc.get("usable_epoch_count", arc["epoch_count"])); ready+=r; failed+=f
        st = "not_generated" if r==0 and f==0 else ("ready" if r>=exp and f==0 else ("failed" if r==0 and f>0 else "partial"))
        per_arc.append({"prn": arc["prn"], "arc_index": arc["arc_index"], "expected": exp, "ready": r, "failed": f, "status": st, "raw_epoch_count": arc.get("raw_epoch_count", arc["epoch_count"]), "planned_interpolation_count": exp, "low_point_epoch_count": arc.get("low_point_epoch_count", 0)})
    planned=sum(int(a.get("usable_epoch_count", a["epoch_count"])) for a in eligible)
    geom = prepare_grid_geometry(grid_step_deg)
    return {"eligible_arc_count": len(eligible), "ineligible_arc_count": len(arcs)-len(eligible), "planned_map_count": planned, "ready_map_count": ready, "missing_map_count": max(0, planned-ready-failed), "failed_map_count": failed, "cache_compatible": bool(validation and validation.compatible), "cache_completed": bool(metadata.get("completed", False)), "method": metadata.get("interpolation_method", METHOD), "projection": metadata.get("projection", PROJECTION), "grid_step_deg": float(grid_step_deg), "grid_lat_count": int(len(geom.lat_values)), "grid_lon_count": int(len(geom.lon_values)), "grid_cell_count": int(len(geom.lat_values)*len(geom.lon_values)), "minimum_epoch_ipp_count": minimum_epoch_ipp_count, "arcs": per_arc}


@app.get("/api/interpolation/epoch")
async def interpolation_epoch(prn: str = Query(...), time_h: float = Query(...), grid_step_deg: float = Query(DEFAULT_GRID_STEP_DEG)) -> dict[str, object]:
    cache_path = _require_cache_path()
    with connect_cache(cache_path) as con:
        row = con.execute("SELECT epoch_index, time_h FROM prn_epochs WHERE prn = ? ORDER BY ABS(epoch_index - ROUND(? * 120)), time_h LIMIT 1", [prn, time_h]).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No cached epochs are available for PRN {prn}.")
    epoch_index, actual_time_h = int(row[0]), float(row[1])
    grid_step_deg = _validated_grid_step_or_422(grid_step_deg)
    arcs = [a for a in eligible_arcs_from_daily(cache_path) if str(a["prn"]) == prn and int(round(float(a["start_time_h"])*120)) <= epoch_index <= int(round(float(a["end_time_h"])*120))]
    if not arcs:
        raise HTTPException(status_code=404, detail=f"No interpolation arc contains PRN {prn} epoch {epoch_index}.")
    arc = arcs[0]
    from tid_analyzer.interpolation.orchestrator import _cache_dir_for_daily
    cache_dir = _cache_dir_for_daily(state.cache_dir, cache_path, grid_step_deg)
    status = get_epoch_status(cache_dir, prn=prn, arc_index=int(arc["arc_index"]), epoch_index=epoch_index)
    if status is None:
        raise HTTPException(status_code=404, detail=f"No interpolation grid is cached for PRN {prn} epoch {epoch_index}.")
    payload = read_epoch_result(cache_dir, prn=prn, arc_index=int(arc["arc_index"]), epoch_index=epoch_index)
    base = {"prn": prn, "requested_time_h": time_h, "actual_time_h": actual_time_h, "epoch_index": epoch_index, "available": status == "ready", "method": payload["method"], "projection": payload["projection"], "grid_step_deg": payload["grid_step_deg"], "point_count": payload["point_count"], "station_count": payload["station_count"], "status": status}
    if status != "ready":
        return {**base, "message": payload.get("message", "")}
    return {**base, "lon_values": payload["lon_values"].tolist(), "lat_values": payload["lat_values"].tolist(), "values": payload["values"].tolist(), "valid_mask": payload["valid_mask"].tolist()}


@app.post("/api/select-folder")
async def select_folder() -> dict[str, str | None]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="tkinter is unavailable; use manual folder path entry instead.") from exc
    try:
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True); folder = filedialog.askdirectory(); root.destroy()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not open folder selection dialog: {exc}") from exc
    return {"folder_path": folder or None}


import_subscribers: set[WebSocket] = set()


async def _import_broadcaster() -> None:
    while True:
        update = await state.queue.get()
        stale: list[WebSocket] = []
        for websocket in list(import_subscribers):
            try:
                await websocket.send_json({**update, "operation": "import"})
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            import_subscribers.discard(websocket)


@app.on_event("startup")
async def start_import_broadcaster() -> None:
    asyncio.create_task(_import_broadcaster())


@app.websocket("/ws/import-progress")
async def import_progress(websocket: WebSocket) -> None:
    await websocket.accept(); import_subscribers.add(websocket); await websocket.send_json({**state.status, "operation": "import"})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        import_subscribers.discard(websocket)


@app.websocket("/ws/interpolation-progress")
async def interpolation_progress(websocket: WebSocket) -> None:
    await websocket.accept(); await websocket.send_json(interpolation_state.status)
    try:
        while True:
            update = await interpolation_state.queue.get(); await websocket.send_json(update)
    except WebSocketDisconnect:
        return
