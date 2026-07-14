from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError
from pathlib import Path

import uvicorn


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def frontend_dir() -> Path:
    candidates = [repository_root() / "frontend", Path.cwd() / "frontend"]
    for candidate in candidates:
        if (candidate / "package.json").exists():
            return candidate
    return candidates[0]


def resolve_npm() -> str | None:
    names = ["npm.cmd", "npm"] if os.name == "nt" else ["npm"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _url_responds(url: str, timeout: float = 0.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def wait_for_servers_and_open(host: str, port: int, frontend_url: str, timeout_seconds: float = 30.0) -> bool:
    backend_url = f"http://{host}:{port}/api/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _url_responds(backend_url) and _url_responds(frontend_url):
            webbrowser.open(frontend_url)
            return True
        time.sleep(0.25)
    print(f"Warning: browser was not opened because backend {backend_url} and frontend {frontend_url} were not both ready within {timeout_seconds:.0f}s.", file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local TID Analyzer backend and open the browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend-url", default="http://localhost:5173")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--start-frontend", action="store_true", help="Also run `npm run dev` in frontend/.")
    args = parser.parse_args()

    frontend_proc: subprocess.Popen[bytes] | None = None
    if args.start_frontend:
        npm = resolve_npm()
        if npm is None:
            print("npm was not found. Install Node.js LTS, then run setup_windows.ps1.", file=sys.stderr)
            raise SystemExit(1)
        front = frontend_dir()
        if not (front / "node_modules").exists():
            print("frontend/node_modules not found. Run setup_windows.ps1 or cd frontend; npm install", file=sys.stderr)
            raise SystemExit(1)
        frontend_proc = subprocess.Popen([npm, "run", "dev"], cwd=front)

    if not args.no_browser:
        threading.Thread(target=lambda: wait_for_servers_and_open(args.host, args.port, args.frontend_url), daemon=True).start()

    try:
        uvicorn.run("tid_analyzer.api.app:app", host=args.host, port=args.port, reload=False)
    finally:
        if frontend_proc is not None:
            frontend_proc.terminate()
            frontend_proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
