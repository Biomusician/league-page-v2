"""Facts from the awards engine, without the awards.

The engine computes ten awards and reached no reader, because publishing an
award means picking a winner and that is the Commissioner's job. These are
the same underlying numbers stated as what they are: superlatives, with a
team, a value and evidence. Nothing here declares anybody the winner of
anything, and these tests are mostly about keeping it that way.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.site_build import build_site
from leaguepage.storage import Storage
from leaguepage.week_leaders import BENCH_FLOOR, week_leaders

from season import populate_season

DISCO = get_league("disco")
SEASON = "2027"
NAMES = {rid: f"Team {rid}" for rid in range(1, 13)}
SLUGS = {rid: f"team-{rid}" for rid in range(1, 13)}


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("leaders")
    s = Storage(tmp / "t.sqlite3")
    s.__enter__()
    populate_season(s, DISCO, teams=12, weeks_played=5, current_week=5,
                    season=SEASON, seed=3)
    yield s
    s.__exit__(None, None, None)


def _by_label(rows):
    return {r["label"]: r for r in rows}


def test_a_week_that_was_played_produces_the_headline_numbers(played):
    rows = week_leaders(played, DISCO, 3, NAMES, SLUGS)
    got = _by_label(rows)
    assert "Highest score" in got
    assert "Biggest margin" in got
    assert "Closest game" in got


def test_nothing_is_published_before_a_game_is_played(tmp_path):
    """Preseason has rows in the schedule and no points in them. The correct
    output is nothing, not zeros."""
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=0, current_week=1,
                        season=SEASON, seed=3)
        assert week_leaders(s, DISCO, 1, NAMES, SLUGS) == []


def test_every_row_carries_evidence(played):
    """A number without provenance is not publishable here."""
    for r in week_leaders(played, DISCO, 3, NAMES, SLUGS):
        assert r["evidence"], r["label"]
        assert any(e.startswith("sleeper:") for e in r["evidence"]), r


def test_the_highest_score_really_is_the_highest(played):
    from leaguepage.matchup_analysis import analyze_week

    rows = week_leaders(played, DISCO, 4, NAMES, SLUGS)
    best = float(_by_label(rows)["Highest score"]["value"])
    every = [t["points"] for m in analyze_week(played, DISCO, 4)["matchups"]
             for t in m["teams"] if t["points"] is not None]
    assert best == pytest.approx(max(every))


def test_a_blowout_and_a_nailbiter_are_never_the_same_game(played):
    for week in (1, 2, 3, 4, 5):
        got = _by_label(week_leaders(played, DISCO, week, NAMES, SLUGS))
        if "Closest game" in got and "Biggest margin" in got:
            assert got["Closest game"]["value"] != got["Biggest margin"]["value"]


def test_the_margin_rows_credit_the_winner(played):
    got = _by_label(week_leaders(played, DISCO, 2, NAMES, SLUGS))
    for label in ("Biggest margin", "Closest game"):
        if label in got:
            assert float(got[label]["value"]) > 0, label


def test_the_bench_row_clears_its_own_floor(played):
    for week in (1, 2, 3, 4, 5):
        got = _by_label(week_leaders(played, DISCO, week, NAMES, SLUGS))
        if "Most left on the bench" in got:
            assert float(got["Most left on the bench"]["value"]) >= BENCH_FLOOR


def test_a_benched_kicker_is_never_the_story(played, monkeypatch):
    """Twenty lines from the bench-swap code that already refuses to do
    this: you start your only kicker, so a benched one outscoring him is not
    a decision anybody made."""
    real = played.get_player

    def all_kickers(pid):
        return {**(real(pid) or {}), "position": "K"}

    monkeypatch.setattr(played, "get_player", all_kickers)
    for week in (1, 3, 5):
        got = _by_label(week_leaders(played, DISCO, week, NAMES, SLUGS))
        assert "Most left on the bench" not in got


# ------------------------------------------------- the publication boundary

AWARD_WORDS = re.compile(r"\b(award|winner|wins the|nominee|nomination|"
                         r"Manager of the Week|Galaxy Brain|Hard-Luck|"
                         r"Benchwarmer Memorial|Shame)\b", re.I)


def test_no_row_declares_a_winner_of_anything(played):
    """Deployment authority covers deterministic public-safe improvements.
    It does not cover handing out awards."""
    for r in week_leaders(played, DISCO, 3, NAMES, SLUGS):
        for field in ("label", "detail"):
            assert not AWARD_WORDS.search(r[field]), (r["label"], r[field])


def test_the_private_nomination_slate_is_untouched(played):
    """The Desk still gets its ten awards with their slates; the public
    surface is a parallel view, not a replacement."""
    from leaguepage.weekly_awards import weekly_award_nominations

    awards = weekly_award_nominations(played, DISCO, 3)
    keys = {a["award_key"] for a in awards}
    assert {"shame", "manager-of-the-week", "galaxy-brain"} <= keys
    assert all("slate" in a or not a["nominees"] for a in awards)


def test_the_module_reaches_the_front_page(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=5, current_week=5,
                        season=SEASON, seed=3)
        populate_season(s, get_league("surfeit"), teams=10, weeks_played=5,
                        current_week=5, season=SEASON, seed=9)
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    home = (tmp_path / "dist" / "disco" / "index.html").read_text(encoding="utf-8")
    assert "Week 5 in numbers" in home
    assert "Highest score" in home
    # and it does not push the editorial lead off the top
    assert home.find('class="card lead"') < home.find('class="module leaders"')
