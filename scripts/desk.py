"""Run the Commissioner's Desk on localhost.

Usage: .venv/Scripts/python.exe scripts/desk.py [--port 8026]
Localhost-only by design — the Desk's privacy model is that it never leaves
this machine.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from leaguepage.desk import create_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8026)
    args = parser.parse_args()
    print(f"Commissioner's Desk: http://127.0.0.1:{args.port}/commissioner")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
