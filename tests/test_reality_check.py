"""Who is actually good, without the arithmetic trap.

An undefeated team CANNOT have a record below its all-play unless its
all-play is also perfect. Any naive "outperforming its scoring" test
therefore nominates every unbeaten team and every winless one by arithmetic
rather than by luck, and a line calling the league's best scorer lucky is
wrong about the season. Most of these tests are about that.
"""
from __future__ import annotations

import pytest

from leaguepage.config import get_league
from leaguepage.reality_check import MIN_GAP_GAMES, MIN_WEEKS, _line, _phrase
from leaguepage.storage import Storage

from season import populate_season

DISCO = get_league("disco")
SEASON = "2027"
NAMES = {rid: f"Team {rid}" for rid in range(1, 13)}


def _rc(storage, week):
    from leaguepage.reality_check import reality_check

    return reality_check(storage, DISCO, week, NAMES, {})


@pytest.fixture(scope="module")
def season(tmp_path_factory):
    s = Storage(tmp_path_factory.mktemp("rc") / "t.sqlite3")
    s.__enter__()
    populate_season(s, DISCO, teams=12, weeks_played=7, current_week=7,
                    season=SEASON, seed=3)
    yield s
    s.__exit__(None, None, None)


# ------------------------------------------------------------ the sample

def test_nothing_is_published_before_the_number_means_anything(tmp_path):
    """One bad Sunday moves this by a whole game in week 2."""
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=MIN_WEEKS - 1,
                        current_week=MIN_WEEKS - 1, season=SEASON, seed=3)
        assert _rc(s, MIN_WEEKS - 1) is None


def test_the_denominator_is_stated_not_implied(season):
    rc = _rc(season, 7)
    # 7 weeks against 11 opponents, not 7 games.
    assert "77 games per team instead of 7" in rc["note"]
    for r in rc["rows"]:
        assert r["games"] and r["weeks"]


def test_a_short_window_is_flagged_rather_than_ranked_silently(season):
    """A team with a bye played fewer all-play games than the league, so its
    percentage is over a smaller denominator."""
    rc = _rc(season, 7)
    league_weeks = max(r["weeks"] for r in rc["rows"])
    for r in rc["rows"]:
        assert r["short_sample"] is (r["weeks"] < league_weeks)


# ------------------------------------------------------- the arithmetic trap

def _rec(w, l, t=0):
    return {"wins": w, "losses": l, "ties": t}


def _ap(pct, w=None, l=None):
    w = w if w is not None else int(pct * 100)
    l = l if l is not None else 100 - w
    return {"pct": pct, "wins": w, "losses": l, "games": w + l}


def test_an_unbeaten_team_that_scores_like_it_is_not_called_lucky():
    """The best scoring team in the league leading the league is the season
    working correctly."""
    line = _line(_rec(5, 0), _ap(0.85), gap=1.4, played=5)
    assert "real" in line
    assert "worse" not in line and "lucky" not in line


def test_an_unbeaten_team_on_a_mediocre_all_play_is_told_why_it_qualifies():
    line = _line(_rec(5, 0), _ap(0.55), gap=2.2, played=5)
    assert "Every team that wins them all is ahead of its scoring" in line
    assert "55%" in line


def test_a_winless_team_that_deserves_it_is_told_so():
    line = _line(_rec(0, 7), _ap(0.12), gap=-0.9, played=7)
    assert "This is not bad luck" in line


def test_a_winless_team_that_does_not_deserve_it_is_told_that_instead():
    line = _line(_rec(0, 7), _ap(0.45), gap=-3.1, played=7)
    assert "45%" in line and "worse than the scoring earned" in line


def test_a_record_that_matches_its_scoring_says_nothing_dramatic():
    line = _line(_rec(4, 3), _ap(0.56), gap=0.1, played=7)
    assert line == "The record and the scoring agree."


def test_an_ordinary_gap_reads_as_a_sentence_not_a_metric():
    hot = _line(_rec(6, 1), _ap(0.62, 48, 29), gap=1.6, played=7)
    assert hot.startswith("Two games better than the scoring earned")
    assert "48-29" in hot
    cold = _line(_rec(3, 4), _ap(0.64, 49, 28), gap=-1.5, played=7)
    assert cold.startswith("Two games worse than the scoring earned")


@pytest.mark.parametrize("gap,said", [
    (0.9, "about a game"), (1.4, "about a game"),
    (1.6, "two games"), (2.4, "two games"),
    (3.0, "three games"), (4.2, "4 games"),
])
def test_a_gap_is_said_the_way_it_would_be_said_out_loud(gap, said):
    assert _phrase(gap) == said
    assert _phrase(-gap) == said


# ------------------------------------------------------------- the extremes

def test_the_two_ends_clear_the_stated_floor(season):
    rc = _rc(season, 7)
    for end in ("luckiest", "unluckiest"):
        if rc[end]:
            assert abs(rc[end]["gap"]) >= MIN_GAP_GAMES, end


def test_the_hot_end_really_is_the_hottest(season):
    rc = _rc(season, 7)
    assert rc["luckiest"]["gap"] == max(r["gap"] for r in rc["rows"])
    assert rc["unluckiest"]["gap"] == min(
        r["gap"] for r in rc["rows"] if abs(r["gap"]) >= MIN_GAP_GAMES)


def test_a_league_where_nobody_is_lucky_publishes_nothing(monkeypatch, season):
    """The correct output for a league whose records match its scoring is no
    module, not two cards saying nothing happened."""
    from leaguepage import reality_check as rcmod

    monkeypatch.setattr(rcmod, "MIN_GAP_GAMES", 99.0)
    assert rcmod.reality_check(season, DISCO, 7, NAMES, {})["luckiest"] is None


def test_the_module_reaches_the_standings_page(tmp_path, monkeypatch):
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp
    from leaguepage.site_build import build_site

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=7, current_week=7,
                        season=SEASON, seed=3)
        populate_season(s, get_league("surfeit"), teams=10, weeks_played=7,
                        current_week=7, season=SEASON, seed=9)
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    page = (tmp_path / "dist" / "disco" / "standings" / "index.html").read_text(
        encoding="utf-8")
    assert "Reality Check" in page
    assert "Running hot" in page and "Running cold" in page
    assert "games per team instead of" in page
