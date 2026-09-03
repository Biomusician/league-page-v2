"""The season-state matrix: build the whole site at seven points in a year.

Every module on this site changes shape with the calendar, and the failure
mode is never an exception — it is a page that renders a row of zeroes in
August, or claims a playoff race in week 2, or quietly drops a section in
December. So this walks a synthetic season from preseason to the playoffs
and asserts, at each stop, the things a reader would notice.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.front_page import season_state
from leaguepage.site_build import audit_output, build_site
from leaguepage.storage import Storage

from season import SEASON_STATES, populate_season

DISCO = get_league("disco")
SURFEIT = get_league("surfeit")
SEASON = "2027"


def _text(path):
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


@pytest.fixture
def matrix_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    return tmp_path


def _build_state(tmp_path, label, weeks_played, week, pws):
    db = tmp_path / f"{label}.sqlite3"
    out = tmp_path / f"dist-{label}"
    with Storage(db) as s:
        info = {
            "disco": populate_season(s, DISCO, teams=12, weeks_played=weeks_played,
                                     current_week=week, playoff_week_start=pws,
                                     season=SEASON, seed=11),
            "surfeit": populate_season(s, SURFEIT, teams=10, weeks_played=weeks_played,
                                       current_week=week, playoff_week_start=pws,
                                       season=SEASON, seed=23),
        }
        result = build_site(s, out_dir=out, published_dir=tmp_path / "published",
                            editorial_dir=tmp_path / "editorial")
    return out, result, info


@pytest.mark.parametrize("label,weeks_played,week,pws", SEASON_STATES)
def test_site_builds_and_stays_private_in_every_state(matrix_env, label, weeks_played, week, pws):
    out, result, _ = _build_state(matrix_env, label, weeks_played, week, pws)
    assert result["pages"], f"{label}: built nothing"
    assert audit_output(out) == [], f"{label}: privacy violations in the build"


@pytest.mark.parametrize("label,weeks_played,week,pws", SEASON_STATES)
def test_no_page_renders_a_placeholder_in_any_state(matrix_env, label, weeks_played, week, pws):
    """A rendered `None`, `nan` or `{{` is the classic season-state defect:
    a field that only exists once games have been played."""
    out, _, _ = _build_state(matrix_env, label, weeks_played, week, pws)
    bad = []
    for page in sorted(out.rglob("*.html")):
        body = _text(page)
        for needle in (" None ", " nan ", "{{", "{%", " inf ", "N/A%"):
            if needle in body:
                bad.append(f"{page.relative_to(out)}: {needle!r}")
    assert bad == [], f"{label}: {bad[:8]}"


@pytest.mark.parametrize("label,weeks_played,week,pws", SEASON_STATES)
def test_standings_agree_with_the_scores_that_were_written(matrix_env, label, weeks_played, week, pws):
    """The standings page is derived from roster settings; the season builder
    derived those from the box scores. A disagreement here means a page is
    reading a different source than the rest of the site."""
    out, _, info = _build_state(matrix_env, label, weeks_played, week, pws)
    for slug, teams in (("disco", 12), ("surfeit", 10)):
        body = _text(out / slug / "standings" / "index.html")
        recs = info[slug]["records"]
        wins = sum(r["wins"] for r in recs.values())
        losses = sum(r["losses"] for r in recs.values())
        assert wins == losses, f"{label}/{slug}: {wins} wins vs {losses} losses"
        expected_games = weeks_played * (teams // 2)
        assert wins + sum(r["ties"] for r in recs.values()) // 2 * 0 == expected_games or weeks_played == 0
        best = max(recs.values(), key=lambda r: (r["wins"], r["pf"]))
        if weeks_played:
            assert f"{best['wins']}" in body


@pytest.mark.parametrize("label,weeks_played,week,pws", SEASON_STATES)
def test_playoff_outlook_waits_for_a_real_sample(matrix_env, label, weeks_played, week, pws):
    """Percentages off two games are fiction. The site says so instead of
    printing them."""
    out, _, _ = _build_state(matrix_env, label, weeks_played, week, pws)
    body = _text(out / "disco" / "standings" / "index.html")
    if weeks_played < 3:
        assert "%" not in body.split("Playoff")[-1][:400] or "opens after" in body
    else:
        assert "playoff" in body.lower()


def test_season_state_labels_track_games_not_the_calendar():
    """A league whose week counter advanced but whose games are all zeroes is
    still preseason to a reader."""
    assert season_state(0, 6, 15) == "preseason"
    assert season_state(1, 1, 15) == "opening"
    assert season_state(5, 5, 15) == "midseason"
    assert season_state(13, 13, 15) == "playoff_race"
    assert season_state(14, 15, 15) == "postseason"


@pytest.mark.parametrize("label,weeks_played,week,pws", SEASON_STATES)
def test_front_page_is_never_half_empty(matrix_env, label, weeks_played, week, pws):
    """`front_page.build` suppresses the briefing below its floor rather than
    padding it. Either it is absent or it carries at least MIN_ITEMS."""
    from leaguepage.front_page import MAX_ITEMS, MIN_ITEMS
    out, _, _ = _build_state(matrix_env, label, weeks_played, week, pws)
    for slug in ("disco", "surfeit"):
        html = (out / slug / "index.html").read_text(encoding="utf-8")
        cards = html.count('class="brief-card')
        assert cards == 0 or MIN_ITEMS <= cards <= MAX_ITEMS, \
            f"{label}/{slug}: {cards} briefing cards"
