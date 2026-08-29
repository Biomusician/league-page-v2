"""Mechanical style check for league prose files.

Usage: .venv/Scripts/python.exe scripts/style_check.py <file> [<file> ...]
Exit code is the warning count capped at 1 with --strict, else 0 (warnings only).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.style_check import check_text, format_report


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--strict"]
    strict = "--strict" in sys.argv
    total = 0
    for arg in args:
        text = Path(arg).read_text(encoding="utf-8")
        warnings = check_text(text)
        total += len(warnings)
        print(f"== {arg}")
        print(format_report(warnings))
    return 1 if (strict and total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
