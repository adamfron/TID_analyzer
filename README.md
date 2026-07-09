# TID Analyzer

Initial MVP scaffold for a local Python-backed web application for GNSS travelling ionospheric disturbance analysis from preprocessed vertical dTEC text files.

## Data format

Daily folders contain station files named like `LAMA_2024_246.txt`. Rows are semicolon-separated:

1. time in hours
2. PRN
3. vertical dTEC
4. satellite azimuth in degrees
5. satellite elevation in degrees
6. IPP longitude in degrees
7. IPP latitude in degrees

The MVP streams files line by line, indexes metadata, applies GPS-only and elevation >= 50° defaults, and writes `.tid_analyzer_cache/day_manifest.json`.

## Development

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
- `WS /ws/import-progress`
