"""Index the historical newsletter archive into SQLite.

The archive lives in git as plain text: archive/<league_slug>/<file>.md.
Each file carries a minimal frontmatter block written at export time:

    ---
    league: disco
    season: 2021
    week: 4
    title: 2021 Disco Week 4
    ---
    <issue text>

Files without frontmatter are still indexed — league from the directory name,
season/week guessed from the filename where possible. Re-running is idempotent
(keyed on the repo-relative path).

archive/provenance.json preserves the source-document audit trail (original
Drive title, created/modified timestamps, season-inference confidence and
notes). It is merged into the index but never drives the inference itself —
frontmatter stays authoritative for league/season/week.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from leaguepage.config import ARCHIVE_DIR, REPO_ROOT
from leaguepage.storage import Storage

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SEASON_RE = re.compile(r"\b(20\d\d)\b")
_WEEK_RE = re.compile(r"week[\s_-]*(\d{1,2})", re.IGNORECASE)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ({key: value}, body). Empty dict if no frontmatter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    return meta, text[m.end():]


def load_provenance(archive_dir: Path = ARCHIVE_DIR) -> dict[str, dict]:
    path = archive_dir / "provenance.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("files", {})


def index_file(storage: Storage, path: Path, provenance: dict[str, dict] | None = None) -> dict:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    league_slug = meta.get("league") or path.parent.name.lower()
    title = meta.get("title") or path.stem.replace("-", " ").replace("_", " ")

    season = meta.get("season")
    if not season:
        m = _SEASON_RE.search(path.stem)
        season = m.group(1) if m else None

    week: int | None = None
    raw_week = meta.get("week")
    if raw_week and raw_week.isdigit():
        week = int(raw_week)
    else:
        m = _WEEK_RE.search(path.stem)
        if m:
            week = int(m.group(1))

    source_path = path.relative_to(REPO_ROOT).as_posix()
    prov = (provenance or {}).get(source_path, {})
    storage.upsert_archive_issue(
        league_slug=league_slug,
        season=season,
        week=week,
        title=title,
        source_path=source_path,
        body=body.strip(),
        doc_created=prov.get("doc_created"),
        doc_modified=prov.get("doc_modified"),
        dating_confidence=prov.get("dating_confidence"),
        dating_note=prov.get("dating_note") or None,
    )
    return {"path": source_path, "league": league_slug, "season": season, "week": week, "title": title}


def index_archive(storage: Storage, archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    provenance = load_provenance(archive_dir)
    indexed = []
    for path in sorted(archive_dir.rglob("*.md")):
        indexed.append(index_file(storage, path, provenance))
    return indexed
