from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.parser import build_manifest


@dataclass
class ImportState:
    cache_dir: Path = field(default_factory=lambda: Path(".tid_analyzer_cache"))
    status: dict[str, Any] = field(default_factory=lambda: {"stage": "idle", "current": 0, "total": 0, "message": "Idle"})
    manifest: dict[str, Any] | None = None
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None

    async def publish(self, update: dict[str, Any]) -> None:
        self.status = update
        await self.queue.put(update)

    async def start_import(self, folder: Path) -> None:
        if self.task and not self.task.done():
            raise RuntimeError("An import is already running")
        self.task = asyncio.create_task(self._run_import(folder))

    async def _run_import(self, folder: Path) -> None:
        loop = asyncio.get_running_loop()

        def progress(stage: str, current: int, total: int, message: str) -> None:
            update = {"stage": stage, "current": current, "total": total, "message": message}
            asyncio.run_coroutine_threadsafe(self.publish(update), loop)

        try:
            manifest = await asyncio.to_thread(build_manifest, folder, self.cache_dir, ImportFilters(), progress)
            self.manifest = manifest
        except Exception as exc:  # noqa: BLE001 - message is surfaced to local UI
            await self.publish({"stage": "error", "current": 0, "total": 0, "message": str(exc)})
