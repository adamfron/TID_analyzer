from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn


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
        frontend_proc = subprocess.Popen(["npm", "run", "dev"], cwd="frontend")

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
