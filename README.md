# TID Analyzer

Initial MVP scaffold for a local Python-backed web application for GNSS travelling ionospheric disturbance analysis from preprocessed vertical dTEC text files.

## Data format

Daily folders contain station files named like `LAMA_2024_246.txt`. Rows are semicolon-separated and may include a trailing semicolon:

1. time in hours
2. PRN
3. vertical dTEC
4. satellite azimuth in degrees
5. satellite elevation in degrees
6. IPP longitude in degrees
7. IPP latitude in degrees

The MVP streams files line by line, indexes metadata, applies GPS-only, elevation >= 50°, and Europe-region map bounds defaults, and writes `.tid_analyzer_cache/day_manifest.json`.

## Windows quick start

First setup:

```powershell
.\setup_windows.ps1
```

Later runs:

```powershell
.\run_windows.ps1
```

If the repo is updated, run `setup_windows.ps1` again when dependencies change. Otherwise, `run_windows.ps1` is enough.

Do not open `frontend/index.html` directly. The backend runs on `127.0.0.1:8000`, and the frontend runs on `127.0.0.1:5173`.

## Manual development

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd frontend
npm install
npm run dev
```

POSIX shells:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Backend:

```bash
tid-analyzer --no-browser
```

To run both and open the browser:

```bash
tid-analyzer --start-frontend
```

API endpoints:

- `POST /api/import` with `{ "folder_path": "/path/to/day" }`
- `GET /api/import/status`
- `GET /api/manifest`
- `GET /api/preview/points`
- `POST /api/select-folder`
- `WS /ws/import-progress`

## Exploratory workflow

The web workspace now follows a numbered exploratory flow:

1. **Data import & summary** imports a daily station folder and reports parser/filter metadata.
2. **Satellite visibility** groups filtered GPS observations into per-PRN visibility arcs, splitting arcs on configurable time gaps.
3. **Map explorer** draws a lightweight lon/lat SVG map with bundled world borders under IPP points. Modes include current epoch, whole selected satellite, and selected time window. Current-epoch requests explicitly preserve `time_h=0`.
4. **Station time series** lets map clicks select station/PRN pairs and overlays selected vertical dTEC series.
5. **Spectral analysis**, **TID candidates**, and **Event report** are placeholders for later FFT/wavelet, detection, tracking, and export work.

Useful API endpoints for this stage:

- `GET /api/assets/world-borders`
- `GET /api/satellites/visibility?gap_minutes=10`
- `GET /api/preview/points?prn=G24&time_h=0&tolerance_seconds=15`
- `GET /api/preview/points?prn=G24&start_time_h=1&end_time_h=2`
- `GET /api/stations/timeseries?station=LAMA&station=GWWL&prn=G24`
