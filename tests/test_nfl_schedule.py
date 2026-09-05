"""The NFL schedule is reference data, and byes are read from it, not guessed.

The brief used to say "byes: not available" on every card. A season's
schedule under refdata/nfl/ changes that to a statement: this starter's
team does not play this week. No file for the season means the old honest
answer, never an empty set dressed up as "everybody plays".
"""
from __future__ import annotations

import json

import pytest

from leaguepage import nfl_schedule
from leaguepage.nfl_schedule import game_for, load_schedule, opponent_label, teams_on_bye


@pytest.fixture
def sched_dir(tmp_path):
    rows = [
        {"season": 2027, "week": 1, "game_type": "REG", "home": "SEA", "away": "NE", "gameday": "2027-09-08"},
        {"season": 2027, "week": 1, "game_type": "REG", "home": "GB", "away": "DET", "gameday": "2027-09-08"},
        {"season": 2027, "week": 2, "game_type": "REG", "home": "NE", "away": "DET", "gameday": "2027-09-15"},
        # a playoff row must not count as a regular-season game
        {"season": 2027, "week": 19, "game_type": "WC", "home": "SEA", "away": "GB", "gameday": "2028-01-10"},
    ]
    (tmp_path / "schedule_2027.json").write_text(json.dumps(
        {"season": 2027, "fetched_at": "2027-09-01T00:00:00+00:00", "rows": rows}),
        encoding="utf-8")
    load_schedule.cache_clear()
    yield tmp_path
    load_schedule.cache_clear()


def test_a_team_without_a_game_is_on_bye(sched_dir):
    assert teams_on_bye(2027, 2, schedule_dir=sched_dir) == {"SEA", "GB"}
    assert teams_on_bye(2027, 1, schedule_dir=sched_dir) == set()


def test_no_schedule_means_unknown_not_everyone_plays(sched_dir):
    assert teams_on_bye(2026, 1, schedule_dir=sched_dir) is None
    assert teams_on_bye(2027, 9, schedule_dir=sched_dir) is None, "a week the file lacks"


def test_opponent_and_venue(sched_dir):
    g = game_for(2027, 1, "NE", schedule_dir=sched_dir)
    assert g == {"opponent": "SEA", "home": False, "gameday": "2027-09-08"}
    assert opponent_label(g) == "at SEA"
    assert opponent_label(game_for(2027, 1, "SEA", schedule_dir=sched_dir)) == "vs NE"
    assert opponent_label(game_for(2027, 2, "SEA", schedule_dir=sched_dir)) == "bye"


def test_playoff_rows_are_not_regular_season(sched_dir):
    s = load_schedule(2027, sched_dir)
    assert 19 not in s["by_week"]


def test_the_real_2026_file_is_present_and_complete():
    """The one on disk covers the season this product is publishing."""
    s = load_schedule(2026)
    assert s is not None, "refdata/nfl/schedule_2026.json"
    assert len(s["teams"]) == nfl_schedule.NFL_TEAMS
    assert set(s["by_week"]) == set(range(1, 19))
    assert teams_on_bye(2026, 1) == set(), "no byes in week 1"
    weeks_with_byes = [w for w in range(1, 19) if teams_on_bye(2026, w)]
    assert weeks_with_byes, "bye weeks exist mid-season"


def test_the_research_brief_reads_byes_from_the_schedule(sched_dir, monkeypatch):
    from leaguepage import matchup_research as research

    monkeypatch.setattr(nfl_schedule, "SCHEDULE_DIR", sched_dir)

    class FakeStorage:
        def get_rosters(self, league_id):
            return [{"roster_id": 1, "players": ["p1", "p2"], "starters": ["p1"]}]

        def get_player(self, pid):
            return {"p1": {"full_name": "Bye Starter", "position": "WR", "team": "SEA"},
                    "p2": {"full_name": "Fine Bench", "position": "RB", "team": "NE",
                           "injury_status": "Questionable"}}[pid]

    class Lg:
        league_id = "x"

    team = {"roster_id": 1, "starters": ["p1"]}
    lines = research.availability(FakeStorage(), Lg(), team, season=2027, week=2)
    assert lines[0].startswith("  BYE — WR Bye Starter (STARTING")
    assert any("QUESTIONABLE" in l and "bench" in l for l in lines)
    assert "SEA" in research.bye_note(2027, 2) and "GB" in research.bye_note(2027, 2)
    assert research.bye_note(2027, 1).startswith("  byes: none this week")
    assert "not available" in research.bye_note(2026, 1)
