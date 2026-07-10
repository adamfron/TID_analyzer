from __future__ import annotations

from collections import defaultdict
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tid_analyzer.config import ImportFilters
from tid_analyzer.api.state import ImportState
from tid_analyzer.importer.cache import connect_cache
from tid_analyzer.importer.parser import StationRow, build_manifest, iter_station_files, iter_valid_rows

app = FastAPI(title="TID Analyzer API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
state = ImportState()


class ImportRequest(BaseModel):
    folder_path: str


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
    clauses = ["UPPER(station) IN (" + ",".join("?" for _ in requested) + ")", "prn = ?"]
    params: list[object] = [*sorted(requested), prn]
    if start_time_h is not None:
        clauses.append("time_h >= ?")
        params.append(start_time_h)
    if end_time_h is not None:
        clauses.append("time_h <= ?")
        params.append(end_time_h)
    sql = "SELECT station, prn, time_h, epoch_index, dtec, azimuth, elevation, ipp_lon, ipp_lat FROM observations WHERE " + " AND ".join(clauses) + " ORDER BY station, time_h"
    grouped: dict[str, list[StationRow]] = {name: [] for name in requested}
    with connect_cache(_require_cache_path()) as con:
        for tup in con.execute(sql, params).fetchall():
            row = _row_from_tuple(tup)
            grouped[row.station.upper()].append(row)
    series = []
    for name in sorted(grouped):
        rows = grouped[name]
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
