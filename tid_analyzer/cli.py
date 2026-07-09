from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
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
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(args.frontend_url)), daemon=True).start()

    try:
        uvicorn.run("tid_analyzer.api.app:app", host=args.host, port=args.port, reload=False)
    finally:
        if frontend_proc is not None:
            frontend_proc.terminate()
            frontend_proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
