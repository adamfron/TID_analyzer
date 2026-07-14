from __future__ import annotations

import asyncio
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import connect_cache
from tid_analyzer.interpolation.natural_neighbor import DEFAULT_GRID_STEP_DEG, METHOD, PROJECTION, interpolate_prn_epoch_natural_neighbor
from tid_analyzer.interpolation.storage import (
    ArcDescriptor,
    create_or_open_interpolation_cache,
    interpolation_cache_dir,
    mark_cache_complete,
    write_epoch_result,
)

TERMINAL_FAILED = {"failed", "geometry_error"}
FAILED_STATUSES = {"failed", "geometry_error", "insufficient_points"}


@dataclass(frozen=True)
class InterpolationJob:
    prn: str
    arc_index: int
    epoch_index: int
    time_h: float


@dataclass
class InterpolationController:
    cache_dir: Path = field(default_factory=lambda: Path(".tid_analyzer_cache"))
    status: dict[str, Any] = field(default_factory=lambda: _idle_status())
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    max_workers: int = field(default_factory=lambda: _max_workers())

    async def publish(self, update: dict[str, Any]) -> None:
        self.status = update
        await self.queue.put(update)

    async def start_build(self, *, daily_cache_path: Path, retry_failed: bool = False, force_rebuild: bool = False) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise RuntimeError("An interpolation build is already running")
        plan = build_interpolation_plan(cache_root=self.cache_dir, daily_cache_path=daily_cache_path, retry_failed=retry_failed, force_rebuild=force_rebuild)
        self.cancel_event.clear()
        self.task = asyncio.create_task(self._run_build(daily_cache_path=daily_cache_path, jobs=plan["jobs"], retry_failed=retry_failed, already_ready=int(plan["already_ready_count"])))
        return {k: plan[k] for k in ("eligible_arc_count", "planned_epoch_count", "already_ready_count", "remaining_epoch_count")}

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.cancel_event.set()
            await self.publish({**self.status, "state": "cancelling", "message": "Interpolation cancellation requested"})
        else:
            await self.publish({**_idle_status(), "state": "cancelled", "message": "No interpolation build is running"})

    async def _run_build(self, *, daily_cache_path: Path, jobs: list[InterpolationJob], retry_failed: bool, already_ready: int = 0) -> None:
        total = len(jobs)
        generated = skipped = failed = current = 0
        cache_dir = _cache_dir_for_daily(self.cache_dir, daily_cache_path)
        try:
            await self.publish(_status("running", 0, total, generated, already_ready, skipped, failed, "Starting interpolation build"))
            sem = asyncio.Semaphore(self.max_workers)
            for job in jobs:
                if self.cancel_event.is_set():
                    await self.publish(_status("cancelled", current, total, generated, already_ready, skipped, failed, "Interpolation build cancelled"))
                    return
                current += 1
                await self.publish(_status("running", current, total, generated, already_ready, skipped, failed, f"Interpolating {job.prn} arc {job.arc_index} epoch {job.epoch_index}", job))
                async with sem:
                    result = await asyncio.to_thread(_compute_job, daily_cache_path, job)
                write_epoch_result(cache_dir, result, arc_index=job.arc_index)
                if result.status == "ready":
                    generated += 1
                elif result.status == "insufficient_points":
                    skipped += 1
                else:
                    failed += 1
            if not self.cancel_event.is_set():
                mark_cache_complete(cache_dir)
                await self.publish(_status("completed", total, total, generated, already_ready, skipped, failed, "Interpolation build completed"))
        except Exception as exc:  # noqa: BLE001
            await self.publish(_status("error", current, total, generated, already_ready, skipped, failed, str(exc)))


def _idle_status() -> dict[str, Any]:
    return _status("idle", 0, 0, 0, 0, 0, 0, "Idle")


def _status(state: str, current: int, total: int, generated: int, already_ready: int, skipped: int, failed: int, message: str, job: InterpolationJob | None = None) -> dict[str, Any]:
    pct = round((current / total) * 100, 2) if total else 0
    return {"operation": "interpolation", "state": state, "current": current, "total": total, "percent": pct, "current_prn": job.prn if job else None, "current_arc_index": job.arc_index if job else None, "current_epoch_index": job.epoch_index if job else None, "generated": generated, "already_ready": already_ready, "skipped": skipped, "failed": failed, "message": message}


def _max_workers() -> int:
    raw = os.getenv("TID_ANALYZER_INTERPOLATION_WORKERS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, min(4, os.cpu_count() or 1))


def _daily_metadata(daily_cache_path: Path) -> tuple[int, int, float]:
    with connect_cache(daily_cache_path) as con:
        row = con.execute("SELECT year, doy, min_elevation_deg FROM metadata LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("Daily cache metadata is missing")
    return int(row[0]), int(row[1]), float(row[2])


def _cache_dir_for_daily(cache_root: Path, daily_cache_path: Path) -> Path:
    y, d, elev = _daily_metadata(daily_cache_path)
    return interpolation_cache_dir(cache_root, y, d, elev)


def eligible_arcs_from_daily(daily_cache_path: Path, gap_minutes: float = 10.0, min_stations: int = 100, min_duration_min: float = 120.0) -> list[dict[str, Any]]:
    from tid_analyzer.api.app import _visibility_arc_from_epochs  # keep rules in one place
    gap_epochs = gap_minutes * 60 / 30
    arcs: list[dict[str, Any]] = []
    with connect_cache(daily_cache_path) as con:
        rows = con.execute("""
            SELECT prn, epoch_index, AVG(time_h) AS time_h, COUNT(*) AS row_count,
                   COUNT(DISTINCT station) AS station_count, LIST(DISTINCT station ORDER BY station) AS stations
            FROM observations GROUP BY prn, epoch_index ORDER BY prn, epoch_index
        """).fetchall()
    current=[]; arc_index=1; prev_prn=None; prev_epoch=None
    for prn, epoch_index, time_h, row_count, station_count, stations in rows:
        prn=str(prn); epoch_index=int(epoch_index)
        starts = prev_prn is not None and prn != prev_prn
        gap = prev_epoch is not None and epoch_index - prev_epoch > gap_epochs
        if current and (starts or gap):
            arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current)); arc_index = 1 if starts else arc_index + 1; current=[]
        current.append((prn, epoch_index, float(time_h), int(row_count), int(station_count), tuple(str(s) for s in (stations or []))))
        prev_prn=prn; prev_epoch=epoch_index
    if current: arcs.append(_visibility_arc_from_epochs(current[0][0], arc_index, current))
    return arcs


def build_interpolation_plan(*, cache_root: Path, daily_cache_path: Path, retry_failed: bool = False, force_rebuild: bool = False) -> dict[str, Any]:
    year, doy, elev = _daily_metadata(daily_cache_path)
    arcs = eligible_arcs_from_daily(daily_cache_path)
    eligible = [a for a in arcs if a.get("eligible_for_interpolation") is True]
    descriptors = [ArcDescriptor(str(a["prn"]), int(a["arc_index"]), int(a["epoch_count"])) for a in eligible]
    cache_dir = interpolation_cache_dir(cache_root, year, doy, elev)
    if force_rebuild and cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir = create_or_open_interpolation_cache(cache_root=cache_root, daily_cache_path=daily_cache_path, year=year, doy=doy, minimum_elevation_deg=elev, arcs=descriptors)
    jobs_by_key: dict[tuple[str, int, int], InterpolationJob] = {}
    already_ready = skipped_existing = 0
    with connect_cache(daily_cache_path) as con:
        for arc in eligible:
            rows = con.execute("SELECT epoch_index, AVG(time_h) FROM observations WHERE prn = ? AND epoch_index BETWEEN ? AND ? GROUP BY epoch_index ORDER BY epoch_index", [arc["prn"], int(round(float(arc["start_time_h"])*120)), int(round(float(arc["end_time_h"])*120))]).fetchall()
            for epoch_index, time_h in rows:
                key = (str(arc["prn"]), int(arc["arc_index"]), int(epoch_index))
                status = get_epoch_status(cache_dir, prn=key[0], arc_index=key[1], epoch_index=key[2])
                if status == "ready":
                    already_ready += 1; continue
                if status == "insufficient_points" and not retry_failed:
                    skipped_existing += 1; continue
                if status in TERMINAL_FAILED and not retry_failed:
                    skipped_existing += 1; continue
                jobs_by_key.setdefault(key, InterpolationJob(key[0], key[1], key[2], float(time_h)))
    return {"jobs": list(jobs_by_key.values()), "eligible_arc_count": len(eligible), "planned_epoch_count": len(jobs_by_key)+already_ready+skipped_existing, "already_ready_count": already_ready, "remaining_epoch_count": len(jobs_by_key)}


def get_epoch_status(cache_dir: Path, *, prn: str, arc_index: int, epoch_index: int) -> str | None:
    path = cache_dir / f"{prn}_arc_{arc_index}.zarr"
    if not path.exists(): return None
    import zarr
    g = zarr.open_group(str(path), mode="r")
    if epoch_index >= g["status"].shape[0]: return None
    v = g["status"][epoch_index]
    return v.decode() if isinstance(v, bytes) else str(v)


def _compute_job(daily_cache_path: Path, job: InterpolationJob):
    with duckdb.connect(str(daily_cache_path), read_only=True) as con:
        rows = con.execute("SELECT station, ipp_lon, ipp_lat, dtec, time_h FROM observations WHERE prn = ? AND epoch_index = ? ORDER BY station", [job.prn, job.epoch_index]).fetchall()
    data = [{"station": r[0], "ipp_lon": r[1], "ipp_lat": r[2], "dtec": r[3]} for r in rows]
    time_h = float(rows[0][4]) if rows else job.time_h
    return interpolate_prn_epoch_natural_neighbor(prn=job.prn, epoch_index=job.epoch_index, time_h=time_h, rows=data)
