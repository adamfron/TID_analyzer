from __future__ import annotations

import csv
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URLS = [
    "https://epncb.oma.be/ftp/station/coord/EPN/EPN_A_IGS20.SSC",
    "https://epncb.oma.be/ftp/station/coord/EPN/EPN_A_IGS20_short.SSC",
]

@dataclass(frozen=True)
class StationCoordinate:
    station: str
    full_site_id: str | None = None
    city: str | None = None
    country: str | None = None
    domes: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    height: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    coordinate_source: str = "EPN SSC"
    reference_frame: str | None = None
    coordinate_epoch: str | None = None
    resolved: bool = False
    resolution_note: str = "unresolved"


def station_code_from_filename(path: Path) -> str:
    return path.name.split("_", 1)[0].strip().upper()[:4]


def xyz_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        h = p / math.cos(lat) - n
        new_lat = math.atan2(z, p * (1 - e2 * n / (n + h)))
        if abs(new_lat - lat) < 1e-12:
            lat = new_lat
            break
        lat = new_lat
    sin_lat = math.sin(lat)
    n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - n
    return math.degrees(lon), math.degrees(lat), h


def _cache_dir(cache_root: Path) -> Path:
    d = cache_root / "station_catalog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_catalog_files(cache_root: Path) -> list[Path]:
    return sorted(_cache_dir(cache_root).glob("*.SSC")) + sorted(_cache_dir(cache_root).glob("*.ssc"))


def download_catalogs(cache_root: Path, urls: list[str] | None = None) -> list[Path]:
    files: list[Path] = []
    for url in urls or DEFAULT_URLS:
        target = _cache_dir(cache_root) / Path(url).name
        if target.exists():
            files.append(target)
            continue
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                target.write_bytes(response.read())
            files.append(target)
        except Exception:
            continue
    return files


def parse_ssc_file(path: Path) -> dict[str, StationCoordinate]:
    text = path.read_text(errors="ignore")
    frame_match = re.search(r"(IGS\d+|ITRF\d+|ETRF\d+)", text)
    frame = frame_match.group(1) if frame_match else None
    coords: dict[str, StationCoordinate] = {}
    site_pat = re.compile(r"\b([A-Z0-9]{4}00[A-Z0-9]{3})\b")
    num = r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?"
    xyz_pat = re.compile(rf"({num})\s+({num})\s+({num})")
    for line in text.splitlines():
        m = site_pat.search(line.upper())
        if not m:
            continue
        site_id = m.group(1)
        nums = xyz_pat.findall(line)
        if not nums:
            continue
        x, y, z = map(float, nums[-1])
        if max(abs(x), abs(y), abs(z)) < 1_000_000:
            continue
        lon, lat, h = xyz_to_geodetic(x, y, z)
        code = site_id[:4]
        coords[code] = StationCoordinate(code, site_id, None, None, None, lon, lat, h, x, y, z, f"EPN SSC:{path.name}", frame, None, True, "resolved")
    return coords



EUREF_SOURCE = "Bundled EUREF Permanent GNSS Network CSV"

def bundled_euref_csv_path() -> Path:
    repo_asset = Path(__file__).resolve().parents[2] / "assets" / "world" / "EUREF Permanent GNSS Network.csv"
    if repo_asset.exists():
        return repo_asset
    return Path(__file__).resolve().parents[2] / "tid_analyzer" / "assets" / "world" / "EUREF Permanent GNSS Network.csv"

def _float_or_none(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None

def load_bundled_euref_catalog(csv_path: Path | None = None) -> dict[str, list[StationCoordinate]]:
    path = csv_path or bundled_euref_csv_path()
    if not path.exists():
        return {}
    catalog: dict[str, list[StationCoordinate]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Name") or "").strip().upper()
            if len(name) < 4:
                continue
            code = name[:4]
            lat = _float_or_none(row.get("Latitude")); lon = _float_or_none(row.get("Longitude"))
            h = _float_or_none(row.get("Elevation"))
            resolved = lon is not None and lat is not None
            status = (row.get("Status") or "").strip()
            coord = StationCoordinate(
                station=code, full_site_id=name, city=(row.get("City") or "").strip() or None,
                country=(row.get("Country") or "").strip() or None, domes=(row.get("Domes") or "").strip() or None,
                longitude=lon, latitude=lat, height=h, x=_float_or_none(row.get("X")), y=_float_or_none(row.get("Y")), z=_float_or_none(row.get("Z")),
                coordinate_source=EUREF_SOURCE, resolved=resolved, resolution_note=f"resolved; status={status}" if resolved else f"missing coordinates; status={status}",
            )
            catalog.setdefault(code, []).append(coord)
    return catalog

def _choose_euref_match(code: str, matches: list[StationCoordinate]) -> StationCoordinate:
    included = [m for m in matches if "status=included" in m.resolution_note.lower()]
    candidates = included or matches
    candidates = sorted(candidates, key=lambda m: (m.full_site_id or ""))
    chosen = candidates[0]
    note = chosen.resolution_note
    if len(candidates) > 1:
        names = ", ".join(m.full_site_id or m.station for m in candidates)
        note = f"{note}; ambiguous {code} matches ({names}); selected {chosen.full_site_id}"
    return StationCoordinate(code, chosen.full_site_id, chosen.city, chosen.country, chosen.domes, chosen.longitude, chosen.latitude, chosen.height, chosen.x, chosen.y, chosen.z, chosen.coordinate_source, chosen.reference_frame, chosen.coordinate_epoch, chosen.resolved, note)

def load_catalog(cache_root: Path, allow_download: bool = True) -> dict[str, StationCoordinate]:
    files = cached_catalog_files(cache_root)
    if allow_download and not files:
        files = download_catalogs(cache_root)
    catalog: dict[str, StationCoordinate] = {}
    for f in files:
        catalog.update(parse_ssc_file(f))
    return catalog


def resolve_stations(station_codes: list[str], cache_root: Path, allow_download: bool = True) -> list[StationCoordinate]:
    euref = load_bundled_euref_catalog()
    ssc_catalog = load_catalog(cache_root, allow_download) if allow_download else load_catalog(cache_root, False)
    resolved = []
    for raw in sorted({c.strip().upper()[:4] for c in station_codes if c.strip()}):
        matches = euref.get(raw, [])
        if matches:
            resolved.append(_choose_euref_match(raw, matches))
            continue
        hit = ssc_catalog.get(raw) if len(raw) >= 4 else None
        if hit:
            resolved.append(StationCoordinate(raw, hit.full_site_id, hit.city, hit.country, hit.domes, hit.longitude, hit.latitude, hit.height, hit.x, hit.y, hit.z, hit.coordinate_source, hit.reference_frame, hit.coordinate_epoch, True, "resolved by optional SSC fallback"))
        else:
            note = "unusual station code length" if len(raw) != 4 else "not found in bundled EUREF CSV"
            resolved.append(StationCoordinate(raw, resolved=False, resolution_note=note))
    return resolved
