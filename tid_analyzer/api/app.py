from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    import pywt
except ImportError:  # pragma: no cover - dependency is declared for runtime installs.
    pywt = None

from tid_analyzer.config import ImportFilters
from tid_analyzer.api.state import ImportState
from tid_analyzer.importer.cache import connect_cache
from tid_analyzer.importer.parser import StationRow, build_manifest, iter_station_files, iter_valid_rows

app = FastAPI(title="TID Analyzer API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
state = ImportState()


class ImportRequest(BaseModel):
    folder_path: str
    min_elevation_deg: float = 50.0


class SpectralRequest(BaseModel):
    station: str
    prn: str
    start_time_h: float | None = None
    end_time_h: float | None = None
    period_min_min: float = 2
    period_min_max: float = 180


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


@app.get("/api/manifest")
async def get_manifest() -> dict[str, object]:
    if state.manifest is None:
        raise HTTPException(status_code=404, detail="No manifest is available yet. Start an import first.")
    return state.manifest


@app.get("/api/assets/world-borders")
async def world_borders() -> Response:
    try:
        asset = resources.files("tid_analyzer").joinpath("assets/world/TM_WORLD_BORDERS-0.3.geojson")
        text = asset.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Bundled world borders GeoJSON asset is missing: tid_analyzer/assets/world/TM_WORLD_BORDERS-0.3.geojson") from exc
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
    station_markers = _station_markers(matching) if mode == "current_epoch" else []
    return {"points": [row.__dict__ for row in sampled], "station_markers": station_markers, "interpolated_dtec": None, "raster_available": False, "requested_time_h": time_h, "actual_time_h": actual_time_h, "count_returned": len(sampled), "total_matching_before_limit": len(matching), "limit_reached": limit_reached, "mode_used": mode}


@app.get("/api/satellites/visibility")
async def satellite_visibility(gap_minutes: float = Query(10, gt=0)) -> dict[str, object]:
    cache_path = _require_cache_path()
    gap_epochs = gap_minutes * 60 / 30
    arcs: list[dict[str, object]] = []
    with connect_cache(cache_path) as con:
        rows = con.execute("SELECT prn, epoch_index, time_h, row_count, station_count FROM prn_epochs ORDER BY prn, epoch_index").fetchall()
    current: list[tuple[str, int, float, int, int]] = []
    arc_index = 1
    previous_prn: str | None = None
    previous_epoch: int | None = None
    for prn, epoch_index, time_h, row_count, station_count in rows:
        prn = str(prn); epoch_index = int(epoch_index)
        starts_new_prn = previous_prn is not None and prn != previous_prn
        gap_split = previous_epoch is not None and epoch_index - previous_epoch > gap_epochs
        if current and (starts_new_prn or gap_split):
            arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current))
            arc_index = 1 if starts_new_prn else arc_index + 1
            current = []
        current.append((prn, epoch_index, float(time_h), int(row_count), int(station_count)))
        previous_prn = prn
        previous_epoch = epoch_index
    if current:
        arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current))
    return {"arcs": arcs}


def _station_markers(rows: list[StationRow]) -> list[dict[str, object]]:
    by_station: dict[str, list[StationRow]] = defaultdict(list)
    for row in rows:
        by_station[row.station].append(row)
    markers: list[dict[str, object]] = []
    for station in sorted(by_station):
        station_rows = by_station[station]
        markers.append({
            "station": station,
            "lon": sum(row.ipp_lon for row in station_rows) / len(station_rows),
            "lat": sum(row.ipp_lat for row in station_rows) / len(station_rows),
            "approximate": True,
            "source": "mean_epoch_ipp",
        })
    return markers


def _visibility_arc_from_epochs(prn: str, arc_index: int, rows: list[tuple[str, int, float, int, int]]) -> dict[str, object]:
    start = rows[0][2]
    end = rows[-1][2]
    return {"prn": prn, "arc_index": arc_index, "start_time_h": start, "end_time_h": end, "duration_min": (end - start) * 60, "row_count": sum(r[3] for r in rows), "station_count": max(r[4] for r in rows), "epoch_count": len(rows)}


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
    return {"prn": prn, "requested_time_h": time_h, "actual_time_h": actual_time_h, "epoch_index": epoch_index, "points": points, "count": len(points), "stations": sorted({str(p["station"]) for p in points})}


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


@app.websocket("/ws/import-progress")
async def import_progress(websocket: WebSocket) -> None:
    await websocket.accept(); await websocket.send_json(state.status)
    try:
        while True:
            update = await state.queue.get(); await websocket.send_json(update)
    except WebSocketDisconnect:
        return
