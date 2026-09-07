"""`baseline_text` never leaves the authenticated Desk.

WHAT IT IS AND WHY IT EXISTS

When a section arrives generated, the Desk keeps a private copy of the
text as it stood before the Commissioner touched it. That copy is the
only way the Desk can say how far his version has moved from the draft it
started as. It is research about his own editing, and nothing else.

WHY THIS FILE EXISTS

Tranche 5C moves it into cloud editorial state, where a row is a row and
a careless `select *` reaches further than a careful one. The rule --
"the renderer does not use it" -- is a statement about today's code, and
a test that asserts it by reading the renderer would pass for exactly as
long as nobody adds a field.

So this proves it the other way round. A distinctive sentence is planted
as a baseline, a real site is built from real published snapshots, and
every byte of output is searched for it: the snapshot JSON, every other
public JSON, every reader HTML page, and the whole static tree that would
be deployed. Then the unauthenticated surfaces are asked directly.

If a future field leaks it, the sentinel turns up and this fails, without
anyone having to have predicted which field.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import provenance
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.publish import publish_assembled_issue
from leaguepage.site_build import build_site
from leaguepage.storage import Storage

from fixtures import approve, populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")

# Deliberately unlike anything the fixtures or the templates would emit.
SENTINEL = "Quadrangulated bezoar of the mezzanine, verbatim and unpublished."
# No apostrophe: the renderer escapes one to &#39;, and the positive
# control below would then fail to find its own text on the page -- which
# looks exactly like a leak test passing for the right reason when it is
# really passing for the wrong one.
PUBLISHED = "These words are the Commissioner writing, and they publish.\n"


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A published, built site whose Lowdown has a private baseline."""
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [],
                                 "relationships": []})
    db = tmp_path / "private.sqlite3"
    ldir = ed / SEASON / LG.slug / "week-01" / "lowdown"
    ldir.mkdir(parents=True)
    (ldir / "lowdown.md").write_text(PUBLISHED, encoding="utf-8")

    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete",
                        season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={r: 90.0 + r for r in range(1, 11)})
        s.set_meta("current_week", "1")
        for key in ("hardware", "ctp", "power", "tracks", "fades",
                    "forceflow", "blackbox", "false-assumptions", "branches",
                    "draft-capsules"):
            s.set_issue_module(league_slug=LG.slug, season=SEASON,
                               issue_key="week-01", module_key=key, included=0)
        # The generated draft he started from, kept privately.
        provenance.record(s, league_slug=LG.slug, season=SEASON,
                          issue_key="week-01", section="lowdown",
                          generator="claude-code", method="section-brief",
                          text=SENTINEL, event="marker-arrival")
        row = s.get_prose_provenance(LG.slug, SEASON, "week-01", "lowdown")
        assert row and SENTINEL in (row["baseline_text"] or ""), (
            "the fixture has to actually store a baseline or this file "
            "proves nothing")
        approve(s, league_slug=LG.slug, season=SEASON, issue_key="week-01",
                module_key="lowdown", base_dir=ed)
        publish_assembled_issue(s, LG, SEASON, "week-01", week=1,
                                published_dir=tmp_path / "published",
                                base_dir=ed)
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published", editorial_dir=ed)
    return db, tmp_path


def _offenders(root, needle: str) -> list[str]:
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue                      # images and fonts carry no prose
        if needle in text:
            out.append(path.relative_to(root).as_posix())
    return out


def test_the_published_snapshot_does_not_carry_it(built):
    _db, tmp = built
    snaps = sorted((tmp / "published").rglob("*.json"))
    assert snaps, "nothing was published, so nothing was proved"
    for snap in snaps:
        raw = snap.read_text(encoding="utf-8")
        assert SENTINEL not in raw, f"{snap.name} carries the baseline"
        # And the published prose IS there, so the search is looking at a
        # snapshot with real content in it rather than an empty file.
        assert PUBLISHED.strip() in raw, f"{snap.name} has no prose at all"


def test_no_file_in_the_static_build_carries_it(built):
    """The whole artifact, byte by byte.

    This is the check that does not depend on knowing which field might
    leak: every readable file that would be deployed is searched.
    """
    _db, tmp = built
    dist = tmp / "dist"
    assert (dist / "index.html").exists(), "the site did not build"
    bad = _offenders(dist, SENTINEL)
    assert not bad, f"the baseline reached the deployable artifact: {bad}"


def test_the_reader_pages_carry_the_prose_but_not_its_baseline(built):
    """A negative result is only worth something if the positive one holds."""
    _db, tmp = built
    pages = list((tmp / "dist").rglob("*.html"))
    assert pages, "no reader pages were built"
    assert any(PUBLISHED.strip() in p.read_text(encoding="utf-8")
               for p in pages), (
        "the published words are not on any page, so finding no baseline "
        "proves nothing about pages that carry writing")
    assert not _offenders(tmp / "dist", SENTINEL)


def test_every_public_json_is_clean(built):
    _db, tmp = built
    for path in sorted((tmp / "dist").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert SENTINEL not in json.dumps(payload, default=str), (
            f"{path.name} carries the baseline")


def test_an_unauthenticated_request_cannot_reach_it(built, monkeypatch):
    """The Desk itself, asked without a session.

    Every mutating and reading surface is behind the commissioner
    middleware, so this is really asking whether the public paths leak --
    but asking is cheap and the answer is the one that matters.
    """
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", "boss@example.test")
    monkeypatch.setenv("LEAGUEPAGE_SECRET", "x" * 40)
    db, _tmp = built
    client = TestClient(create_app(db_path=db))
    for path in ("/health", "/login",
                 f"/commissioner/surfeit/{SEASON}/issue/week-01/edit",
                 f"/commissioner/surfeit/{SEASON}/issue/week-01/edit/"
                 "section-state?section=lowdown"):
        r = client.get(path, follow_redirects=False)
        assert SENTINEL not in r.text, f"{path} leaked the baseline"


def test_it_is_still_there_for_the_desk(built):
    """The other half of the guarantee.

    A field that leaked nothing because it was silently dropped would
    pass every test above and quietly break the one feature it exists
    for. It has to survive publication, not merely stay out of it.
    """
    db, _tmp = built
    with Storage(db) as s:
        row = s.get_prose_provenance(LG.slug, SEASON, "week-01", "lowdown")
    assert row and SENTINEL in (row["baseline_text"] or "")


def test_the_model_the_desk_renders_from_never_includes_it(built):
    """One structural check to go with the byte search.

    `section_state` is what the editor card and the published snapshot's
    provenance block are both built from. It is the natural place for
    somebody to add "and show the original", so it is worth pinning by
    name as well as by output.
    """
    db, _tmp = built
    with Storage(db) as s:
        described = provenance.section_state(
            s, league_slug=LG.slug, season=SEASON, issue_key="week-01",
            section="lowdown", text=PUBLISHED)
    assert described, "nothing described, so nothing was checked"
    flat = json.dumps(described, default=str)
    assert SENTINEL not in flat, f"it exposes the baseline: {described}"
    assert "baseline_text" not in flat
