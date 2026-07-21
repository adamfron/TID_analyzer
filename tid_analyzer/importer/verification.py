from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb

from tid_analyzer.config import ImportFilters
from tid_analyzer.importer.cache import aggregate_digest, canonical_json, ensure_identity_columns
from tid_analyzer.importer.parser import _file_sha256, iter_station_files

VerificationState = Literal["verified", "changed", "source_files_unavailable", "legacy_unverified", "verification_error"]

@dataclass(frozen=True)
class VerificationResult:
    state: VerificationState
    imported_database_verified: bool
    raw_source_files_available: bool
    summary: str
    differences: list[str]


def verify_daily_cache(cache_path: Path, source_folder: Path | None = None, *, mode: Literal["fast", "full"] = "fast", filters: ImportFilters | None = None) -> VerificationResult:
    try:
        if not cache_path.exists():
            return VerificationResult("verification_error", False, False, "DuckDB cache is unavailable", ["cache missing"])
        with duckdb.connect(str(cache_path)) as con:
            ensure_identity_columns(con)
            meta = con.execute("SELECT source_folder, completed, application_cache_version, parser_version, source_content_digest, imported_observation_digest, import_filters_json FROM metadata LIMIT 1").fetchone()
            if not meta:
                return VerificationResult("legacy_unverified", False, False, "Legacy cache has no metadata", ["metadata missing"])
            if not meta[4] or not meta[5] or not meta[3]:
                return VerificationResult("legacy_unverified", bool(meta[1]), False, "Legacy cache lacks strong source identity", ["strong digest missing"])
            stored_files = con.execute("SELECT relative_filename, byte_size, mtime_ns, sha256 FROM imported_files ORDER BY relative_filename").fetchall()
            imported_ok = bool(meta[1]) and (filters is None or str(meta[6] or "") == canonical_json(filters.as_manifest_dict()))
            folder = source_folder or Path(str(meta[0]))
            if not folder.exists():
                return VerificationResult("source_files_unavailable", imported_ok, False, "Imported database verified; raw source files unavailable", ["raw source folder missing"])
            current_paths = {p.name: p for p in iter_station_files(folder)}
            stored = {str(r[0]): r for r in stored_files}
            added = sorted(set(current_paths) - set(stored)); removed = sorted(set(stored) - set(current_paths))
            modified = []
            sha_values = []
            for name, row in stored.items():
                if name not in current_paths: continue
                path = current_paths[name]; st = path.stat()
                if mode == "full" or int(row[1] or -1) != st.st_size or int(row[2] or -1) != st.st_mtime_ns:
                    sha = _file_sha256(path)
                else:
                    sha = str(row[3])
                if sha != str(row[3]): modified.append(name)
                sha_values.append(sha)
            for name in added:
                sha_values.append(_file_sha256(current_paths[name]))
            diffs=[]
            if modified: diffs.append(f"{len(modified)} files modified")
            if added: diffs.append(f"{len(added)} file added" if len(added)==1 else f"{len(added)} files added")
            if removed: diffs.append(f"{len(removed)} file removed" if len(removed)==1 else f"{len(removed)} files removed")
            if aggregate_digest(sha_values) != str(meta[4]): diffs.append("source content digest differs")
            if not imported_ok: diffs.append("import configuration differs")
            if diffs:
                return VerificationResult("changed", imported_ok, True, "Input changed:\n" + "\n".join(diffs), diffs)
            return VerificationResult("verified", imported_ok, True, "Imported database verified; raw source files verified", [])
    except Exception as exc:  # noqa: BLE001
        return VerificationResult("verification_error", False, False, f"Verification error: {exc}", [str(exc)])
