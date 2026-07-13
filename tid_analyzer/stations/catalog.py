from __future__ import annotations

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
    return path.name.split("_", 1)[0].strip().upper()


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
        coords[code] = StationCoordinate(code, site_id, lon, lat, h, x, y, z, f"EPN SSC:{path.name}", frame, None, True, "resolved")
    return coords


def load_catalog(cache_root: Path, allow_download: bool = True) -> dict[str, StationCoordinate]:
    files = cached_catalog_files(cache_root)
    if allow_download and not files:
        files = download_catalogs(cache_root)
    catalog: dict[str, StationCoordinate] = {}
    for f in files:
        catalog.update(parse_ssc_file(f))
    return catalog


def resolve_stations(station_codes: list[str], cache_root: Path, allow_download: bool = True) -> list[StationCoordinate]:
    catalog = load_catalog(cache_root, allow_download)
    resolved = []
    for raw in sorted({c.strip().upper() for c in station_codes if c.strip()}):
        hit = catalog.get(raw[:4]) if len(raw) >= 4 else None
        if hit:
            resolved.append(StationCoordinate(raw, hit.full_site_id, hit.longitude, hit.latitude, hit.height, hit.x, hit.y, hit.z, hit.coordinate_source, hit.reference_frame, hit.coordinate_epoch, True, "resolved"))
        else:
            note = "unusual station code length" if len(raw) != 4 else "not found in EPN catalog"
            resolved.append(StationCoordinate(raw, resolved=False, resolution_note=note))
    return resolved
