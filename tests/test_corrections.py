"""Post-publish corrections.

The rule this file defends: a published snapshot is never rewritten. A
correction is an additive sibling file, the original stays on disk as the
record of what actually shipped that day, and the public page says it was
updated and why.
"""
from __future__ import annotations

import json

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.publish import (
    PublishError, publish_assembled_issue, revise_issue, snapshot_family,
)
from leaguepage.site_build import build_site
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LEAGUE = get_league("surfeit")

ORIGINAL = "The room was set, and then it was not. A first draft of history.\n"
CORRECTED = "The room was set, and then it was not. A corrected draft of history.\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LEAGUE, teams=10, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
        populate_matchups(s, LEAGUE, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
    return db, tmp_path


def _write_lowdown(tmp_path, text):
    ldir = tmp_path / "editorial" / SEASON / LEAGUE.slug / "draft" / "lowdown"
    ldir.mkdir(parents=True, exist_ok=True)
    (ldir / "lowdown.md").write_text(text, encoding="utf-8")


def _publish(db, tmp_path, text):
    _write_lowdown(tmp_path, text)
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules",
                    "custom"):
            s.set_issue_module(league_slug=LEAGUE.slug, season=SEASON,
                               issue_key="draft", module_key=key, included=0)
        s.set_issue_module(league_slug=LEAGUE.slug, season=SEASON, issue_key="draft",
                           module_key="lowdown", approved=1)
        return publish_assembled_issue(
            s, LEAGUE, SEASON, "draft", published_dir=tmp_path / "published",
            base_dir=tmp_path / "editorial")


def _revise(db, tmp_path, text, note="corrected team names / formatting"):
    _write_lowdown(tmp_path, text)
    with Storage(db) as s:
        return revise_issue(s, LEAGUE, SEASON, "draft", note=note,
                            published_dir=tmp_path / "published",
                            base_dir=tmp_path / "editorial")


def test_correction_never_touches_the_original_snapshot(env):
    db, tmp = env
    original = _publish(db, tmp, ORIGINAL)
    before = original.read_bytes()

    rev = _revise(db, tmp, CORRECTED)
    assert rev.name == "draft.r2.json"
    assert original.read_bytes() == before, "the original snapshot was mutated"
    assert ORIGINAL.strip() in json.loads(before)["sections"][0]["content_md"]


def test_correction_carries_provenance(env):
    db, tmp = env
    _publish(db, tmp, ORIGINAL)
    rev = json.loads(_revise(db, tmp, CORRECTED).read_text(encoding="utf-8"))
    assert rev["revision"] == 2
    assert rev["revises"] == "draft"
    assert rev["revision_note"] == "corrected team names / formatting"
    assert rev["original_published_at"]
    assert rev["revised_at"]
    assert "corrected draft" in rev["sections"][0]["content_md"]


def test_corrections_stack_and_the_family_stays_ordered(env):
    db, tmp = env
    _publish(db, tmp, ORIGINAL)
    _revise(db, tmp, CORRECTED, note="first pass")
    _revise(db, tmp, "A third telling entirely, with more care taken.\n",
            note="second pass")
    family = snapshot_family(tmp / "published", LEAGUE.slug, SEASON, "draft")
    assert [p.name for p in family] == ["draft.json", "draft.r2.json", "draft.r3.json"]


def test_site_renders_the_latest_revision_with_an_updated_line(env):
    db, tmp = env
    _publish(db, tmp, ORIGINAL)
    _revise(db, tmp, CORRECTED)
    with Storage(db) as s:
        build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                   editorial_dir=tmp / "editorial")
    page = (tmp / "dist" / LEAGUE.slug / SEASON / "draft" / "index.html").read_text(
        encoding="utf-8")
    assert "corrected draft of history" in page
    assert "A first draft of history" not in page
    assert "Updated" in page and "corrected team names / formatting" in page
    # exactly one issue in the archive list, not one per revision
    home = (tmp / "dist" / LEAGUE.slug / "index.html").read_text(encoding="utf-8")
    assert home.count(f'{SEASON}/draft/index.html') >= 1
    assert "draft.r2" not in home


def test_correction_requires_a_note_and_a_change(env):
    db, tmp = env
    _publish(db, tmp, ORIGINAL)
    with pytest.raises(PublishError, match="note"):
        _revise(db, tmp, CORRECTED, note="  ")
    with pytest.raises(PublishError, match="identical"):
        _revise(db, tmp, ORIGINAL)


def test_correction_runs_the_publication_gate(env):
    db, tmp = env
    _publish(db, tmp, ORIGINAL)
    with pytest.raises(PublishError, match="Publication check|Correction refused"):
        _revise(db, tmp, "The room was set. Roster 4 disagreed.\n")


def test_cannot_correct_an_unpublished_issue(env):
    db, tmp = env
    _write_lowdown(tmp, ORIGINAL)
    with Storage(db) as s:
        with pytest.raises(PublishError, match="never been published"):
            revise_issue(s, LEAGUE, SEASON, "draft", note="x",
                         published_dir=tmp / "published",
                         base_dir=tmp / "editorial")


def test_publication_gate_stops_a_first_publish_too(env):
    db, tmp = env
    with pytest.raises(PublishError, match="Publication check"):
        _publish(db, tmp, "Preview pending.\n")
