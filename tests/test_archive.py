from __future__ import annotations

from leaguepage.archive import index_file, parse_frontmatter
from leaguepage.config import REPO_ROOT


ISSUE = """---
league: disco
season: 2021
week: 4
title: 2021 Disco Week 4
---
The Lowdown
Your team has either found its groove or is stuck in a rut.
"""


def test_parse_frontmatter():
    meta, body = parse_frontmatter(ISSUE)
    assert meta["league"] == "disco"
    assert meta["week"] == "4"
    assert body.startswith("The Lowdown")


def test_parse_no_frontmatter():
    meta, body = parse_frontmatter("Just text.")
    assert meta == {}
    assert body == "Just text."


def _write_repo_tmp(name: str, text: str):
    # index_file requires paths inside the repo (source_path is repo-relative)
    path = REPO_ROOT / "tests" / "_tmp_archive" / "disco" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_index_and_search(storage):
    path = _write_repo_tmp("2021-week-04.md", ISSUE)
    try:
        info = index_file(storage, path)
        assert info["season"] == "2021" and info["week"] == 4
        hits = storage.search_archive("groove")
        assert hits and hits[0]["title"] == "2021 Disco Week 4"
        # re-index is idempotent
        index_file(storage, path)
        assert storage.archive_count() == 1
    finally:
        path.unlink()


def test_provenance_merged_into_index(storage):
    path = _write_repo_tmp("2021-week-04.md", ISSUE)
    provenance = {
        "tests/_tmp_archive/disco/2021-week-04.md": {
            "original_title": "2021 Disco Week 4",
            "doc_created": "2021-09-30T09:22:50Z",
            "doc_modified": "2021-09-30T15:04:50Z",
            "dating_confidence": "medium",
            "dating_note": "spot-check me",
        }
    }
    try:
        index_file(storage, path, provenance)
        row = storage.list_archive_issues()[0]
        assert row["doc_created"] == "2021-09-30T09:22:50Z"
        assert row["dating_confidence"] == "medium"
        assert row["dating_note"] == "spot-check me"
        # frontmatter stays authoritative for the inference itself
        assert row["season"] == "2021" and row["week"] == 4
    finally:
        path.unlink()


def test_repo_provenance_covers_every_archive_file():
    import json

    from leaguepage.config import ARCHIVE_DIR, REPO_ROOT

    prov = json.loads((ARCHIVE_DIR / "provenance.json").read_text(encoding="utf-8"))["files"]
    md_files = {p.relative_to(REPO_ROOT).as_posix() for p in ARCHIVE_DIR.rglob("*.md")}
    assert md_files == set(prov.keys())


def test_filename_fallback(storage):
    path = _write_repo_tmp("2022-week-09.md", "No frontmatter here, just a newsletter.")
    try:
        info = index_file(storage, path)
        assert info["league"] == "disco"  # from directory name
        assert info["season"] == "2022" and info["week"] == 9  # from filename
    finally:
        path.unlink()
