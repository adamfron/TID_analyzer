from __future__ import annotations

from collections import defaultdict
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tid_analyzer.config import ImportFilters
from tid_analyzer.api.state import ImportState
from tid_analyzer.importer.parser import StationRow, iter_station_files, iter_valid_rows

app = FastAPI(title="TID Analyzer API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
state = ImportState()


class ImportRequest(BaseModel):
    folder_path: str


def _require_source_folder() -> Path:
    if state.source_folder is None:
        raise HTTPException(status_code=404, detail="No folder has been imported yet.")
    return state.source_folder


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
async def start_import(request: ImportRequest) -> dict[str, str]:
    try:
        await state.start_import(Path(request.folder_path).expanduser())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Import started"}


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
    all_rows = _iter_import_rows()
    matching: list[StationRow] = []
    actual_time_h: float | None = None
    mode = "whole_selected_satellite"
    if time_h is not None:
        mode = "current_epoch"
        candidate_rows = [row for row in all_rows if not prn or row.prn == prn]
        if candidate_rows:
            actual_time_h = min({row.time_h for row in candidate_rows}, key=lambda value: (abs(value - time_h), value))
        for row in candidate_rows:
            if actual_time_h is not None and row.time_h == actual_time_h:
                matching.append(row)
    else:
        for row in all_rows:
            if prn and row.prn != prn:
                continue
            if start_time_h is not None or end_time_h is not None:
                mode = "selected_time_window"
                if start_time_h is not None and row.time_h < start_time_h:
                    continue
                if end_time_h is not None and row.time_h > end_time_h:
                    continue
            matching.append(row)
    matching.sort(key=lambda r: (r.time_h, r.prn, r.station))
    sampled, limit_reached = _deterministic_sample(matching, max_points)
    station_markers = _station_markers(matching) if mode == "current_epoch" else []
    return {"points": [row.__dict__ for row in sampled], "station_markers": station_markers, "interpolated_dtec": None, "raster_available": False, "requested_time_h": time_h, "actual_time_h": actual_time_h, "count_returned": len(sampled), "total_matching_before_limit": len(matching), "limit_reached": limit_reached, "mode_used": mode}


@app.get("/api/satellites/visibility")
async def satellite_visibility(gap_minutes: float = Query(10, gt=0)) -> dict[str, object]:
    by_prn: dict[str, list[StationRow]] = defaultdict(list)
    for row in _iter_import_rows():
        by_prn[row.prn].append(row)
    gap_h = gap_minutes / 60.0
    arcs: list[dict[str, object]] = []
    for prn in sorted(by_prn):
        rows = sorted(by_prn[prn], key=lambda r: r.time_h)
        current: list[StationRow] = []
        arc_index = 1
        previous_time: float | None = None
        for row in rows:
            if current and previous_time is not None and row.time_h - previous_time > gap_h:
                arcs.append(_visibility_arc(prn, arc_index, current))
                arc_index += 1
                current = []
            current.append(row)
            previous_time = row.time_h
        if current:
            arcs.append(_visibility_arc(prn, arc_index, current))
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


def _visibility_arc(prn: str, arc_index: int, rows: list[StationRow]) -> dict[str, object]:
    start = rows[0].time_h
    end = rows[-1].time_h
    return {"prn": prn, "arc_index": arc_index, "start_time_h": start, "end_time_h": end, "duration_min": (end - start) * 60, "row_count": len(rows), "station_count": len({r.station for r in rows})}


@app.get("/api/stations/timeseries")
async def station_timeseries(
    station: list[str] = Query(...),
    prn: str = Query(...),
    start_time_h: float | None = None,
    end_time_h: float | None = None,
    max_points_per_series: int = Query(5000, ge=1, le=100000),
) -> dict[str, object]:
    requested = {part.strip().upper() for value in station for part in value.split(",") if part.strip()}
    if not requested:
        raise HTTPException(status_code=400, detail="At least one station is required.")
    grouped: dict[str, list[StationRow]] = {name: [] for name in requested}
    for row in _iter_import_rows():
        if row.station.upper() not in requested or row.prn != prn:
            continue
        if start_time_h is not None and row.time_h < start_time_h:
            continue
        if end_time_h is not None and row.time_h > end_time_h:
            continue
        grouped[row.station.upper()].append(row)
    series = []
    for name in sorted(grouped):
        rows = sorted(grouped[name], key=lambda r: r.time_h)
        sampled, limit_reached = _deterministic_sample(rows, max_points_per_series)
        series.append({"station": name, "prn": prn, "points": [{"time_h": r.time_h, "dtec": r.dtec, "elevation": r.elevation, "ipp_lon": r.ipp_lon, "ipp_lat": r.ipp_lat} for r in sampled], "total_matching_before_limit": len(rows), "limit_reached": limit_reached})
    return {"series": series}


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
