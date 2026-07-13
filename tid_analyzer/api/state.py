from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.parser import build_manifest, iter_station_files, _detect_day
from tid_analyzer.importer.cache import cache_path_for_day

STAGES = {
    "scanning_files": (1, "Scanning files"),
    "resolving_stations": (2, "Resolving station coordinates"),
    "stations_resolved": (2, "Resolving station coordinates"),
    "validating_input": (3, "Validating input format and filters"),
    "reading_filtering": (4, "Reading and filtering source files"),
    "writing_database": (4, "Reading and filtering source files"),
    "building_indexes": (5, "Building daily database indexes"),
    "visibility_arcs": (6, "Computing satellite visibility arcs"),
    "finalizing_cache": (7, "Finalizing cache"),
    "done": (7, "Finalizing cache"),
    "error": (7, "Finalizing cache"),
    "cancelled": (7, "Finalizing cache"),
}


@dataclass
class ImportState:
    cache_dir: Path = field(default_factory=lambda: Path(".tid_analyzer_cache"))
    status: dict[str, Any] = field(default_factory=lambda: {"stage": "idle", "stage_index": 0, "stage_count": 7, "stage_name": "Idle", "current": 0, "total": 0, "percent": 0, "stage_percent": 0, "overall_percent": 0, "message": "Idle"})
    manifest: dict[str, Any] | None = None
    source_folder: Path | None = None
    cache_path: Path | None = None
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    async def publish(self, update: dict[str, Any]) -> None:
        self.status = update
        await self.queue.put(update)

    def _format_update(self, stage: str, current: int, total: int, message: str) -> dict[str, Any]:
        idx, name = STAGES.get(stage, (0, stage.replace("_", " ").title()))
        stage_percent = round((current / total) * 100, 1) if total else 0
        overall_percent = round(((idx - 1) / 7 + (stage_percent / 100) / 7) * 100, 1) if idx else stage_percent
        if stage == "done":
            stage_percent = overall_percent = 100
        return {"stage": stage, "stage_index": idx, "stage_count": 7, "stage_name": name, "current": current, "total": total, "percent": overall_percent, "stage_percent": stage_percent, "overall_percent": overall_percent, "message": message}

    async def start_import(self, folder: Path, filters: ImportFilters | None = None, force_rebuild: bool = False) -> None:
        if self.task and not self.task.done():
            raise RuntimeError("An import is already running")
        self.cancel_event.clear()
        self.task = asyncio.create_task(self._run_import(folder, filters or ImportFilters(), force_rebuild))

    async def cancel_import(self) -> None:
        if self.task and not self.task.done():
            self.cancel_event.set()
        else:
            await self.publish(self._format_update("cancelled", 0, 0, "Import cancelled"))

    async def _run_import(self, folder: Path, filters: ImportFilters, force_rebuild: bool) -> None:
        loop = asyncio.get_running_loop()
        self.source_folder = folder
        try:
            files = iter_station_files(folder); years, doys = _detect_day(files)
            year = next(iter(years)) if len(years) == 1 else None
            doy = next(iter(doys)) if len(doys) == 1 else None
            self.cache_path = cache_path_for_day(self.cache_dir, year, doy, filters)
        except Exception:
            self.cache_path = None

        def progress(stage: str, current: int, total: int, message: str) -> None:
            asyncio.run_coroutine_threadsafe(self.publish(self._format_update(stage, current, total, message)), loop)

        try:
            manifest = await asyncio.to_thread(build_manifest, folder, self.cache_dir, filters, progress, self.cancel_event.is_set, force_rebuild)
            self.manifest = manifest
            self.source_folder = folder
            cache_path = manifest.get("cache_path")
            self.cache_path = Path(str(cache_path)) if cache_path else None
        except Exception as exc:  # noqa: BLE001 - message is surfaced to local UI
            stage = "cancelled" if "cancelled" in str(exc).lower() else "error"
            await self.publish(self._format_update(stage, 0, 0, str(exc)))
