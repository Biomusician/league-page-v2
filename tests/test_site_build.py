from __future__ import annotations

import json

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
import leaguepage.publish as pub
from leaguepage.config import League, get_league
from leaguepage.publish import publish_assembled_issue
from leaguepage.site_build import audit_output, build_site
from leaguepage.storage import Storage

from fixtures import add_players, populate_league, populate_matchups

SEASON = "2027"  # deliberately not 2026: proves nothing hardcodes the season

TEST_DISCO = get_league("disco")
TEST_SURFEIT = get_league("surfeit")


@pytest.fixture
def site_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, TEST_SURFEIT, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_league(s, TEST_DISCO, teams=12, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
        for lg, teams in ((TEST_SURFEIT, 10), (TEST_DISCO, 12)):
            for wk in (1, 2, 3):
                populate_matchups(s, lg, week=wk, teams=teams,
                                  scores={rid: 90.0 + rid + wk for rid in range(1, teams + 1)})
        s.upsert_archive_issue(
            league_slug="disco", season="2021", week=4, title="2021 Disco Week 4",
            source_path="archive/disco/t.md",
            body="McLovin rode the wagon ruts to victory.\n\nSeasons Past follows.",
            dating_confidence="high", dating_note="internal audit note")
    return db, tmp_path


def _publish_minimal(db, tmp_path, league: League, issue_key: str, text: str):
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules"):
            s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key=issue_key,
                              module_key=key, included=0)
        ldir = tmp_path / "editorial" / SEASON / league.slug / issue_key / "lowdown"
        ldir.mkdir(parents=True, exist_ok=True)
        (ldir / "lowdown.md").write_text(text, encoding="utf-8")
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key=issue_key,
                          module_key="lowdown", approved=1)
        wk = int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None
        return publish_assembled_issue(s, league, SEASON, issue_key, week=wk,
                                       published_dir=tmp_path / "published",
                                       base_dir=tmp_path / "editorial")


def _build(db, tmp_path, **kwargs):
    with Storage(db) as s:
        return build_site(s, out_dir=tmp_path / "dist",
                          published_dir=tmp_path / "published",
                          editorial_dir=tmp_path / "editorial", **kwargs)


def test_root_selector_and_both_league_homes(site_env):
    db, tmp = site_env
    _build(db, tmp)
    root = (tmp / "dist" / "index.html").read_text(encoding="utf-8")
    assert "Disco Chat" in root and "The Surfeit" in root
    for slug, name in (("disco", "DISCO CHAT"), ("surfeit", "THE SURFEIT")):
        home = (tmp / "dist" / slug / "index.html").read_text(encoding="utf-8")
        assert name in home
        assert SEASON in home


def test_season_not_hardcoded(site_env):
    db, tmp = site_env
    _build(db, tmp)
    home = (tmp / "dist" / "surfeit" / "index.html").read_text(encoding="utf-8")
    assert SEASON in home and "2026" not in home


def test_standings_ten_and_twelve_team(site_env):
    db, tmp = site_env
    _build(db, tmp)
    s10 = (tmp / "dist" / "surfeit" / "standings" / "index.html").read_text(encoding="utf-8")
    s12 = (tmp / "dist" / "disco" / "standings" / "index.html").read_text(encoding="utf-8")
    assert s10.count("<tr><td>") == 10          # primary table, one row per team
    assert "Under the Hood" in s10              # advanced view present (weeks played)
    assert s12.count("<tr><td>") == 12
    assert "Team 12" in s12


def test_issue_permalink_latest_home_and_archive_retention(site_env):
    db, tmp = site_env
    _publish_minimal(db, tmp, TEST_SURFEIT, "week-01", "# The Lowdown\n\nWeek one words.")
    _publish_minimal(db, tmp, TEST_SURFEIT, "week-02", "# The Lowdown\n\nWeek two words.")
    _build(db, tmp)
    w1 = (tmp / "dist" / "surfeit" / SEASON / "week-01" / "index.html").read_text(encoding="utf-8")
    w2 = (tmp / "dist" / "surfeit" / SEASON / "week-02" / "index.html").read_text(encoding="utf-8")
    assert "Week one words" in w1 and "Week two words" in w2
    home = (tmp / "dist" / "surfeit" / "index.html").read_text(encoding="utf-8")
    assert "Week two words" in home  # latest issue is the hero
    archive = (tmp / "dist" / "surfeit" / "archive" / "index.html").read_text(encoding="utf-8")
    assert "week-01" in archive and "week-02" in archive  # old links never break


def test_published_issue_immutable_on_rebuild(site_env):
    db, tmp = site_env
    _publish_minimal(db, tmp, TEST_SURFEIT, "week-01", "# The Lowdown\n\nOriginal words.")
    _build(db, tmp)
    page = tmp / "dist" / "surfeit" / SEASON / "week-01" / "index.html"
    original = page.read_text(encoding="utf-8")
    # editorial source changes AFTER publication...
    ldir = tmp / "editorial" / SEASON / "surfeit" / "week-01" / "lowdown"
    (ldir / "lowdown.md").write_text("# The Lowdown\n\nSneaky new words.", encoding="utf-8")
    _build(db, tmp)
    assert page.read_text(encoding="utf-8") == original
    assert "Sneaky" not in page.read_text(encoding="utf-8")


def test_build_excludes_private_material(site_env):
    db, tmp = site_env
    # an unpublished rough draft exists in editorial
    sdir = tmp / "editorial" / SEASON / "surfeit" / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "hardware.md").write_text(
        "<!-- ROUGH DRAFT - COMMISSIONER EDIT REQUIRED -->\nUnpublished award words.",
        encoding="utf-8")
    _build(db, tmp)
    dist = tmp / "dist"
    # no db, no editorial sources, no markdown, nothing but rendered html
    assert not list(dist.rglob("*.sqlite3"))
    assert not list(dist.rglob("*.md"))
    assert not (dist / "editorial").exists()
    blob = "\n".join(p.read_text(encoding="utf-8") for p in dist.rglob("*.html"))
    assert "Unpublished award words" not in blob
    assert "ROUGH DRAFT" not in blob
    assert "sleeper:pick" not in blob and "editorial:manager" not in blob
    assert "dating_confidence" not in blob and "internal audit note" not in blob
    assert audit_output(dist) == []


def test_audit_catches_planted_private_material(site_env):
    db, tmp = site_env
    _build(db, tmp)
    bad = tmp / "dist" / "surfeit" / "bad.html"
    bad.write_text("<p>contact SecretHandle99 at C:/Users/somewhere</p>", encoding="utf-8")
    violations = audit_output(tmp / "dist", extra_forbidden=["SecretHandle99"])
    assert any("SecretHandle99" in v for v in violations)
    assert any("C:/Users" in v for v in violations)


def test_team_routes_and_draft_page(site_env):
    db, tmp = site_env
    _build(db, tmp)
    team = (tmp / "dist" / "surfeit" / "team" / "team-1" / "index.html").read_text(encoding="utf-8")
    assert "Team 1" in team and "Roster" in team
    draft = (tmp / "dist" / "surfeit" / "draft" / "index.html").read_text(encoding="utf-8")
    assert "Full Board" in draft
    assert "Player Number1" in draft
    disco_draft = (tmp / "dist" / "disco" / "draft" / "index.html").read_text(encoding="utf-8")
    assert "36 of 36 picks" in disco_draft  # 12 teams x 3 rounds


def test_historical_archive_issue_renders_verbatim_without_metadata(site_env):
    db, tmp = site_env
    _build(db, tmp)
    archive = (tmp / "dist" / "disco" / "archive" / "index.html").read_text(encoding="utf-8")
    assert "2021 Disco Week 4" in archive
    issue_pages = list((tmp / "dist" / "disco" / "archive").rglob("a*/index.html"))
    assert issue_pages
    page = issue_pages[0].read_text(encoding="utf-8")
    assert "Imported Historical Issue" in page
    assert "wagon ruts" in page                 # verbatim content
    assert "internal audit note" not in page    # provenance stays private
    assert "high" != page  # dating confidence not rendered anywhere meaningful


def test_force_flow_page_distinguishes_log_from_editorial(site_env):
    db, tmp = site_env
    add_players_db = db
    with Storage(add_players_db) as s:
        add_players(s, {"TX1": ("Log Player", "RB", 500)})
        s.save_transactions(TEST_SURFEIT.league_id, 1, [
            {"transaction_id": "t1", "type": "waiver", "status": "complete", "leg": 1,
             "adds": {"TX1": 3}, "drops": {}, "waiver_budget": [{"amount": 5}]}])
    _build(db, tmp)
    page = (tmp / "dist" / "surfeit" / "transactions" / "index.html").read_text(encoding="utf-8")
    assert "Transaction Log" in page and "Log Player" in page
    assert "Moves That Mattered" not in page  # nothing published -> no editorial section


def test_black_box_population_labeling(site_env):
    db, tmp = site_env
    _build(db, tmp)
    page = (tmp / "dist" / "surfeit" / "black-box" / "index.html").read_text(encoding="utf-8")
    assert "weeks 1-3" in page and SEASON in page
    assert "this league only" in page


def test_mobile_and_a11y_basics_in_markup(site_env):
    db, tmp = site_env
    _build(db, tmp)
    home = (tmp / "dist" / "surfeit" / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in home
    assert "max-width" in home and "@media" in home
    assert 'class="skip"' in home                       # keyboard skip link
    assert "prefers-reduced-motion" in home
    assert 'aria-label="League navigation"' in home
    standings = (tmp / "dist" / "surfeit" / "standings" / "index.html").read_text(encoding="utf-8")
    assert 'class="tablewrap"' in standings             # tables scroll in their own viewport


def test_preview_issue_flagged_and_confined_to_preview_build(site_env):
    db, tmp = site_env
    ldir = tmp / "editorial" / SEASON / "surfeit" / "draft" / "lowdown"
    ldir.mkdir(parents=True, exist_ok=True)
    (ldir / "lowdown.md").write_text("# The Lowdown\n\nDraft issue preview words.",
                                     encoding="utf-8")
    _build(db, tmp)  # normal build: unpublished draft issue absent
    assert not (tmp / "dist" / "surfeit" / SEASON / "draft").exists()
    with Storage(db) as s:
        build_site(s, out_dir=tmp / "dist-preview", published_dir=tmp / "published",
                   editorial_dir=tmp / "editorial", preview_issues={"surfeit": "draft"})
    page = (tmp / "dist-preview" / "surfeit" / SEASON / "draft" / "index.html").read_text(encoding="utf-8")
    assert "UNPUBLISHED COMMISSIONER PREVIEW" in page
    assert "Draft issue preview words" in page
