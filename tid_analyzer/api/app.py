from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tid_analyzer.api.state import ImportState

app = FastAPI(title="TID Analyzer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
