from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import os
import statistics
import time
from pathlib import Path

from tid_analyzer.interpolation.natural_neighbor import LAT_BOUNDS, LON_BOUNDS, STAGE_NAMES
from tid_analyzer.interpolation.orchestrator import InterpolationJob, _prepare_jobs_for_submission, _compute_prepared_job, _init_worker, _max_workers, eligible_arcs_from_daily


def _summary(values):
    values = [float(v) for v in values]
    if not values:
        return {"mean": 0.0, "median": 0.0, "p90": 0.0, "p95": 0.0, "maximum": 0.0}
    ordered = sorted(values)
    def pct(p):
        return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))]
    return {"mean": statistics.fmean(values), "median": statistics.median(values), "p90": pct(0.90), "p95": pct(0.95), "maximum": max(values)}


def _jobs(cache: Path, prn: str, epochs: int):
    arcs = [a for a in eligible_arcs_from_daily(cache) if a.get("eligible_for_interpolation") and str(a["prn"]) == prn]
    jobs = []
    for arc in arcs:
        start = int(round(float(arc["start_time_h"]) * 120)); end = int(round(float(arc["end_time_h"]) * 120))
        for epoch in range(start, end + 1):
            jobs.append(InterpolationJob(prn, int(arc["arc_index"]), epoch, epoch / 120.0))
            if len(jobs) >= epochs:
                return jobs
    return jobs


def run(cache: Path, prn: str, epochs: int, matlab_scope: bool = False) -> None:
    workers = _max_workers()
    jobs = _jobs(cache, prn, epochs)
    start = time.perf_counter(); results = []
    payloads = list(_prepare_jobs_for_submission(cache, jobs))
    if workers == 1:
        _init_worker()
        pairs = [_compute_prepared_job(payload) for payload in payloads]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
            pairs = list(pool.map(_compute_prepared_job, payloads))
    results = [result for result, _arc_index in pairs]
    wall = time.perf_counter() - start
    successful = sum(1 for r in results if r.status == "ready")
    skipped = sum(1 for r in results if r.status == "insufficient_points")
    failed = len(results) - successful - skipped
    grid = (len(results[0].lat_values), len(results[0].lon_values)) if results else (0, 0)
    per_map = [r.timings.get("total_epoch_time", 0.0) for r in results]
    print(f"worker count: {workers}")
    print(f"grid dimensions: {grid[0]} x {grid[1]}")
    print(f"epoch count: {len(results)}")
    print(f"mean input points: {statistics.fmean([r.input_row_count for r in results]) if results else 0:.1f}")
    print(f"successful/skipped/failed: {successful}/{skipped}/{failed}")
    print(f"wall time: {wall:.3f} s")
    print(f"maps per minute: {(len(results) / wall * 60) if wall else 0:.2f}")
    s = _summary(per_map)
    print(f"mean/median/p95 seconds per map: {s['mean']:.4f}/{s['median']:.4f}/{s['p95']:.4f}")
    print("stage timing breakdown:")
    for stage in STAGE_NAMES:
        ss = _summary([r.timings.get(stage, 0.0) for r in results])
        print(f"  {stage}: mean={ss['mean']:.4f} median={ss['median']:.4f} p95={ss['p95']:.4f} max={ss['maximum']:.4f}")
    if matlab_scope:
        print("MATLAB-comparable mode: lon -20..30, lat 30..65, step 0.5; no PNGs; 30-second epochs. Application defaults are unchanged.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--prn", default="G24")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--matlab-scope", action="store_true")
    args = parser.parse_args()
    run(args.cache, args.prn, args.epochs, args.matlab_scope)


if __name__ == "__main__":
    main()
