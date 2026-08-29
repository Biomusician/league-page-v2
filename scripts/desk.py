"""Run the Commissioner's Desk on localhost.

Usage: .venv/Scripts/python.exe scripts/desk.py [--port 8026]
Localhost-only by design. Prefer double-clicking "Launch Commissioner
Desk.cmd" in the repo root, which starts this, waits for /health, and
opens the browser.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from leaguepage.desk import create_app, pick_port, probe_health


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8026)
    parser.add_argument("--strict-port", action="store_true",
                        help="fail instead of falling back to a nearby port")
    args = parser.parse_args()

    port, situation = pick_port(args.port)
    if situation == "already-running":
        health = probe_health(port) or {}
        print(f"A Commissioner's Desk is ALREADY RUNNING at "
              f"http://localhost:{port}/commissioner "
              f"(season {health.get('season')}). Not starting a second one.",
              flush=True)
        return 0
    if situation == "fallback":
        if args.strict_port:
            print(f"ERROR: port {args.port} is in use by another program and "
                  f"--strict-port was given.", flush=True)
            return 1
        print(f"NOTE: port {args.port} is in use by another program; "
              f"using {port} instead.", flush=True)

    print(f"Commissioner's Desk: http://localhost:{port}/commissioner", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
