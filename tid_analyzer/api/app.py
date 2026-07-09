from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tid_analyzer.config import ImportFilters
from tid_analyzer.api.state import ImportState
from tid_analyzer.importer.parser import iter_station_files, iter_valid_rows

app = FastAPI(title="TID Analyzer API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
state = ImportState()


class ImportRequest(BaseModel):
    folder_path: str


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


@app.get("/api/preview/points")
async def preview_points(prn: str | None = None, time_h: float | None = None, tolerance_seconds: float = 15, max_points: int = Query(5000, ge=1, le=100000)) -> dict[str, object]:
    if state.source_folder is None:
        raise HTTPException(status_code=404, detail="No folder has been imported yet.")
    tolerance_h = tolerance_seconds / 3600.0
    points: list[dict[str, object]] = []
    limit_reached = False
    for path in iter_station_files(state.source_folder):
        for row in iter_valid_rows(path, ImportFilters()):
            if prn and row.prn != prn:
                continue
            if time_h is not None and abs(row.time_h - time_h) > tolerance_h:
                continue
            if len(points) >= max_points:
                limit_reached = True
                return {"points": points, "count_returned": len(points), "limit_reached": limit_reached}
            points.append(row.__dict__)
    return {"points": points, "count_returned": len(points), "limit_reached": limit_reached}


@app.post("/api/select-folder")
async def select_folder() -> dict[str, str | None]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="tkinter is unavailable; use manual folder path entry instead.") from exc
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory()
        root.destroy()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not open folder selection dialog: {exc}") from exc
    return {"folder_path": folder or None}


@app.websocket("/ws/import-progress")
async def import_progress(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(state.status)
    try:
        while True:
            update = await state.queue.get()
            await websocket.send_json(update)
    except WebSocketDisconnect:
        return
