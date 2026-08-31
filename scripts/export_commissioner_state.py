"""Export every piece of AUTHORITATIVE Commissioner state to one JSON bundle.

This is both the backup mechanism and the migration payload for moving the
authoring store to a hosted database. Recon (2026-08-31) established that
authoritative private state is tiny — ~250 database rows plus ~70 KB of
prose — while ~12,700 rows are Sleeper/archive cache that any sync can
rebuild. Only the authoritative part is exported.

    .venv/Scripts/python.exe scripts/export_commissioner_state.py
    .venv/Scripts/python.exe scripts/export_commissioner_state.py --out path.json

The bundle contains the commissioner's own writing and private editorial
decisions. It is written to backups/ (gitignored) and must never be
committed or deployed. Restore with scripts/import_commissioner_state.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage.config import DB_PATH, EDITORIAL_DIR  # noqa: E402

BUNDLE_VERSION = 1

# Mutable state the commissioner creates and no sync can rebuild.
AUTHORITATIVE_TABLES = [
    "issues", "issue_modules", "prose_revisions", "section_prose_state",
    "team_names", "story_decisions", "award_decisions", "matchup_state",
    "power_rankings", "issue_revision_requests", "takes", "bit_usage",
    "editorial_usage",
]

# meta/ is a key-value grab bag; these prefixes are authoritative editorial
# state, the rest (players_updated_at, current_week...) is sync bookkeeping
# that the next sync rewrites anyway.
AUTHORITATIVE_META_PREFIXES = ("txn_ctx:", "analytics_snapshot:", "deploy_state:")

# Prose lives on the filesystem today. These are the shapes that hold the
# commissioner's own words (see desk_editor's content model); everything
# under generated/, PREP.md and AUTHORING* is derived and rebuildable.
DERIVED_MARKERS = ("/generated/", "/dossiers/")
DERIVED_NAMES = ("PREP.md", "REVIEW_PACKET.md", "REVISION_REQUESTS.md")


def _is_prose(rel: str, name: str) -> bool:
    if any(m in f"/{rel}" for m in DERIVED_MARKERS):
        return False
    if name in DERIVED_NAMES or name.startswith("AUTHORING"):
        return False
    return name.endswith(".md")


def collect(db_path: Path, editorial_dir: Path) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    tables: dict[str, list[dict]] = {}
    for t in AUTHORITATIVE_TABLES:
        if t not in have:
            continue
        tables[t] = [dict(r) for r in con.execute(f'SELECT * FROM "{t}"')]

    meta = {}
    if "meta" in have:
        for k, v in con.execute("SELECT key, value FROM meta"):
            if k.startswith(AUTHORITATIVE_META_PREFIXES):
                meta[k] = v
    con.close()

    prose: dict[str, str] = {}
    if editorial_dir.exists():
        for p in sorted(editorial_dir.rglob("*.md")):
            rel = p.relative_to(editorial_dir).as_posix()
            if _is_prose(rel, p.name):
                prose[rel] = p.read_text(encoding="utf-8")

    # managers.json / coalitions.json are local-only config, not prose, but a
    # restore without them loses private manager context. Carry them so a
    # rebuild is complete; they are the most sensitive part of the bundle.
    config = {}
    for name in ("managers.json", "coalitions.json"):
        f = editorial_dir / name
        if f.exists():
            config[name] = f.read_text(encoding="utf-8")

    payload = {
        "bundle_version": BUNDLE_VERSION,
        "exported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tables": tables,
        "meta": meta,
        "prose": prose,
        "config": config,
    }
    payload["checksum"] = checksum(payload)
    return payload


def checksum(payload: dict) -> str:
    """Stable digest of the content (excludes the timestamp and itself), so a
    round trip can be proven byte-identical."""
    body = {k: payload[k] for k in ("tables", "meta", "prose", "config")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--editorial", type=Path, default=EDITORIAL_DIR)
    args = ap.parse_args()

    payload = collect(args.db, args.editorial)
    out = args.out or (REPO / "backups" /
                       f"commissioner-state-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                   encoding="utf-8")

    rows = sum(len(v) for v in payload["tables"].values())
    print(f"exported {rows} rows across {len(payload['tables'])} tables, "
          f"{len(payload['meta'])} meta keys, {len(payload['prose'])} prose files "
          f"({sum(len(t) for t in payload['prose'].values())/1024:.1f} KB), "
          f"{len(payload['config'])} config files")
    for t, v in sorted(payload["tables"].items()):
        if v:
            print(f"  {t:26s} {len(v):>5}")
    print(f"checksum: {payload['checksum'][:16]}")
    print(f"wrote {out}")
    print("PRIVATE: contains your prose and manager context. Never commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
