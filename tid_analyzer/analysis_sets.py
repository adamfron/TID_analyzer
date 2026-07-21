from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sets_root(cache_root: Path) -> Path:
    return cache_root / "analysis_sets"

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def manifest_path(cache_root: Path, set_id: str) -> Path:
    return sets_root(cache_root) / set_id / "manifest.json"

def load_set(cache_root: Path, set_id: str) -> dict[str, Any]:
    return json.loads(manifest_path(cache_root, set_id).read_text(encoding="utf-8"))

def save_set(cache_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    sid = str(manifest.get("uuid") or uuid.uuid4())
    ts = now()
    manifest = {**manifest, "uuid": sid, "updated_at": ts, "created_at": manifest.get("created_at") or ts}
    path = manifest_path(cache_root, sid); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"); tmp.replace(path)
    return manifest

def create_set(cache_root: Path, *, name: str, source_fingerprint: str | None, grid_step: float, minimum_epoch_ipp_count: int, cache_products: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return save_set(cache_root, {
        "name": name, "description": extra.get("description", ""), "source_fingerprint": source_fingerprint,
        "verification_status": extra.get("verification_status", "legacy_unverified" if not source_fingerprint else "verified"),
        "grid_step": float(grid_step), "minimum_ipp_per_epoch": int(minimum_epoch_ipp_count),
        "interpolation_scope": extra.get("interpolation_scope", "all"), "selected_satellites_or_product_ids": extra.get("selected", []),
        "time_range": extra.get("time_range"), "expected_map_keys": extra.get("expected_map_keys", []),
        "completed_map_keys": extra.get("completed_map_keys", []), "skipped_map_keys": extra.get("skipped_map_keys", []),
        "failed_map_keys": extra.get("failed_map_keys", []), "display_settings": extra.get("display_settings", {}),
        "canonical_zarr_cache_products": cache_products or [], "stale": False,
    })

def list_sets(cache_root: Path) -> list[dict[str, Any]]:
    root = sets_root(cache_root)
    if not root.exists(): return []
    out=[]
    for p in sorted(root.glob("*/manifest.json")):
        try: out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception: pass
    return sorted(out, key=lambda m: str(m.get("updated_at", "")), reverse=True)

def rename_set(cache_root: Path, set_id: str, name: str) -> dict[str, Any]:
    m=load_set(cache_root,set_id); m["name"]=name; return save_set(cache_root,m)

def duplicate_set(cache_root: Path, set_id: str, name: str | None = None) -> dict[str, Any]:
    m=load_set(cache_root,set_id); m.pop("uuid", None); m["name"] = name or f"{m.get('name','Analysis set')} copy"; return save_set(cache_root,m)

def delete_set(cache_root: Path, set_id: str) -> None:
    shutil.rmtree((sets_root(cache_root) / set_id), ignore_errors=True)
