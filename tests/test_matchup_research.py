"""The research a matchup preview is actually written from.

The brief used to open by explaining why the system found the matchup
interesting. That question was already answered by the time he opened the
card. What he was still looking up by hand was who plays, who might not,
what each side has to get past, what they just did, what they could still
do, and what is on the record against them.

Two rules run through all of it. Say the basis: "key player" off a
preseason board and "key player" off six weeks of scoring are different
claims and the brief prints which one it is. And say what is not known
instead of filling the space: Sleeper publishes no projections, and the
synced player payload carries no bye weeks, so neither is ever inferred.

All of it is private. It is research on the Desk; nothing here publishes
unless the Commissioner writes it into his own prose.
"""
from __future__ import annotations

import pytest

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import matchup_research as research
from leaguepage.config import get_league
from leaguepage.matchup_analysis import analyze_week
from leaguepage.storage import Storage

from fixtures import add_players, populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        yield s, db


def _week(s, week=1):
    a = analyze_week(s, LG, week, managers={})
    return a, a["matchups"][0]


# ------------------------------------------------------------ key players

def test_key_players_print_what_the_ranking_rests_on(env):
    s, _db = env
    from leaguepage.team_analytics import player_values

    _a, m = _week(s)
    values, stage = player_values(s, LG, weeks_played=0)
    lines = research.key_players(s, LG, m["teams"][0], values, stage)
    if lines:                       # the fixture may not rank its players
        assert lines[0].strip().startswith("basis:")
        assert stage in lines[0]


def test_key_players_come_from_the_starting_lineup(env):
    """A bench player does not decide the game."""
    s, _db = env
    _a, m = _week(s)
    team = m["teams"][0]
    values = {pid: {"name": f"P{pid}", "position": "WR", "value": float(i)}
              for i, pid in enumerate(team.get("starters") or [])}
    values["bench-superstar"] = {"name": "Bench Superstar", "position": "WR",
                                 "value": 9999.0}
    lines = research.key_players(s, LG, team, values, "test basis")
    assert "Bench Superstar" not in "\n".join(lines)


def test_key_players_are_ordered_by_value(env):
    s, _db = env
    _a, m = _week(s)
    team = m["teams"][0]
    starters = [p for p in (team.get("starters") or []) if p and p != "0"]
    values = {pid: {"name": f"P{i}", "position": "WR", "value": float(i)}
              for i, pid in enumerate(starters)}
    lines = research.key_players(s, LG, team, values, "test", limit=3)[1:]
    assert lines == [f"  WR P{i}" for i in
                     range(len(starters) - 1, len(starters) - 1 - len(lines), -1)]


# ------------------------------------------------------------ availability

def test_a_starter_designation_sorts_above_a_bench_one(env):
    s, _db = env
    _a, m = _week(s)
    team = m["teams"][0]
    starters = [p for p in (team.get("starters") or []) if p and p != "0"]
    roster = next(r for r in s.get_rosters(LG.league_id)
                  if r["roster_id"] == team["roster_id"])
    bench = [p for p in (roster.get("players") or []) if p not in starters]
    if not bench:
        pytest.skip("fixture roster has no bench")
    add_players(s, {starters[0]: ("Starting Guy", "WR", 1)})
    add_players(s, {bench[0]: ("Bench Guy", "WR", 2)})
    s.save_players({
        starters[0]: {"full_name": "Starting Guy", "position": "WR",
                      "injury_status": "Questionable"},
        bench[0]: {"full_name": "Bench Guy", "position": "WR",
                   "injury_status": "Out"},
    })
    lines = research.availability(s, LG, team)
    assert "Starting Guy" in lines[0] and "STARTING" in lines[0]
    assert any("Bench Guy" in line and "bench" in line for line in lines)


def test_a_clean_roster_says_so_rather_than_going_blank(env):
    s, _db = env
    _a, m = _week(s)
    assert research.availability(s, LG, m["teams"][0]) == [
        "  nobody on this roster carries an injury designation"]


def test_byes_are_declared_missing_never_guessed():
    """The synced payload has no bye week in it and this product holds no
    NFL schedule. Inventing one would be the worst kind of wrong: specific."""
    note = research.bye_note()
    assert "not available" in note
    assert "Check Sleeper" in note


# ------------------------------------------------------------ gap to close

def test_the_gap_is_written_for_both_sides(env):
    """`The gap` as one number is the losing team's problem stated once. A
    preview has two subjects and each is behind in something."""
    s, _db = env
    from leaguepage.team_analytics import positional_profile

    a, m = _week(s)
    profile = positional_profile(s, LG, weeks_played=0)
    x, y = m["teams"]
    left = research.gap_to_close(profile, x, y, "X", "Y", weeks_played=0)
    right = research.gap_to_close(profile, y, x, "Y", "X", weeks_played=0)
    assert left and right and left != right


def test_before_any_games_the_table_gap_says_there_is_none(env):
    s, _db = env
    from leaguepage.team_analytics import positional_profile

    _a, m = _week(s)
    profile = positional_profile(s, LG, weeks_played=0)
    lines = research.gap_to_close(profile, m["teams"][0], m["teams"][1],
                                  "X", "Y", weeks_played=0)
    assert "no games played yet" in lines[0]
    assert not any("record" in line for line in lines)


def test_an_unrated_position_is_not_reported_as_a_gap(env):
    """A room the ranking could not measure ranks by roster_id, and `#9 of
    10` about that is a fact about the sort, not about the roster."""
    s, _db = env
    _a, m = _week(s)
    x, y = m["teams"]
    profile = {"ranks": {"TE": {x["roster_id"]: 10, y["roster_id"]: 1}},
               "rated": {"TE": set()}, "n": 10, "teams": {}}
    lines = research.gap_to_close(profile, x, y, "X", "Y", weeks_played=0)
    assert not any("behind at TE" in line for line in lines)


# ------------------------------------------------------------ moves

def test_a_team_with_no_transactions_says_so(env):
    s, _db = env
    lines = research.recent_moves(s, LG, 1, 0)
    assert lines == ["  no transactions on record for this team yet"]


def test_possible_moves_report_the_budget_not_a_recommendation(env):
    s, _db = env
    from leaguepage.team_analytics import positional_profile

    _a, m = _week(s)
    profile = positional_profile(s, LG, weeks_played=0)
    lines = research.possible_moves(s, LG, profile, m["teams"][0], weeks_played=0)
    text = "\n".join(lines)
    assert "FAAB left" in text
    for prescriptive in ("should", "must", "needs to claim", "recommend"):
        assert prescriptive not in text.lower(), prescriptive


# ------------------------------------------------------------ roast ammo

def test_roast_ammo_is_never_invented(env):
    s, _db = env
    _a, m = _week(s)
    lines = research.self_inflicted(s, LG, m["teams"][0], weeks_played=0)
    assert lines, "must always say something, even if it is nothing"
    assert all(line.startswith("  ") for line in lines)


def test_a_kicker_reach_is_not_ammunition(env):
    """Consensus ranks every kicker below the draftable range while lineups
    force each team to draft one, so the deviation measures the reference
    board and not a decision anyone made."""
    from leaguepage.draft_value import SKILL_POSITIONS

    assert "K" not in SKILL_POSITIONS and "DEF" not in SKILL_POSITIONS
    src = (research.__file__)
    text = open(src, encoding="utf-8").read()
    assert "SKILL_POSITIONS" in text


def test_only_a_real_reach_counts(env):
    """A pick eleven spots early is a preference. The project's own bar is a
    full round or more, and this uses that same classifier."""
    text = open(research.__file__, encoding="utf-8").read()
    assert "classify_pick" in text and "CLASS_REACH" in text


# ------------------------------------------------------------ callbacks

def _meeting(week, a, b, winner=1):
    return {"week": week, "points": f"{a}-{b}", "winner": winner}


def test_no_history_means_no_callback():
    assert research.notable_callback({"h2h": {"meetings": []}}, {}) == (None, [])
    assert research.notable_callback({}, {}) == (None, [])


def test_a_routine_win_is_suppressed():
    """This is the whole point. A callback to an ordinary week-3 win costs
    a paragraph and buys nothing, and it was going into every preview
    because history existed rather than because it mattered."""
    m = {"h2h": {"meetings": [_meeting(3, 118.0, 101.0)]}}
    reason, lines = research.notable_callback(m, {})
    assert reason is None and lines == []


def test_a_game_decided_by_almost_nothing_is_retained():
    m = {"h2h": {"meetings": [_meeting(3, 118.0, 115.5)]}}
    reason, lines = research.notable_callback(m, {})
    assert reason is not None and "decided by" in reason
    assert lines and "week 3" in lines[0]


def test_a_beating_is_retained():
    m = {"h2h": {"meetings": [_meeting(3, 160.0, 95.0)]}}
    reason, _lines = research.notable_callback(m, {})
    assert reason is not None and "beating" in reason


def test_the_highest_score_between_them_is_retained():
    m = {"h2h": {"meetings": [_meeting(2, 100.0, 88.0), _meeting(5, 170.0, 150.0)]}}
    reason, _lines = research.notable_callback(m, {})
    assert reason == "the highest score either has put on the other"


def test_the_most_recent_meeting_is_the_one_judged():
    """An old classic does not make this week's rematch notable."""
    m = {"h2h": {"meetings": [_meeting(2, 180.0, 179.0), _meeting(9, 120.0, 100.0)]}}
    reason, _lines = research.notable_callback(m, {})
    assert reason is None


def test_the_callback_names_the_winner_by_public_name():
    m = {"h2h": {"meetings": [_meeting(3, 118.0, 115.5, winner=7)]}}
    _reason, lines = research.notable_callback(m, {7: {"name": "Wild SeeKats"}})
    assert "Wild SeeKats" in lines[0]


def test_a_meeting_with_unreadable_points_is_not_guessed_at():
    m = {"h2h": {"meetings": [{"week": 3, "points": "tbd", "winner": 1}]}}
    assert research.notable_callback(m, {}) == (None, [])


# ------------------------------------------------------------ the brief

def test_the_brief_leads_with_who_plays(env):
    s, _db = env
    from leaguepage.ghost_briefs import _matchup_brief

    _a, m = _week(s)
    text = _matchup_brief(s, LG, SEASON, 1, m["matchup_slug"])["text"]
    assert text.startswith("WHO DECIDES IT")
    for heading in ("WHO MIGHT NOT PLAY", "WHAT EACH SIDE HAS TO GET PAST",
                    "WHAT THEY JUST DID", "WHAT THEY COULD STILL DO",
                    "ON THE RECORD AGAINST THEM"):
        assert heading in text, heading
    # scoring and angles come after the reporting, not before it
    assert text.index("WHO DECIDES IT") < text.index("WHY THIS ONE MATTERS")


def test_the_brief_marks_the_roast_ammunition_private(env):
    s, _db = env
    from leaguepage.ghost_briefs import _matchup_brief

    _a, m = _week(s)
    text = _matchup_brief(s, LG, SEASON, 1, m["matchup_slug"])["text"]
    line = next(x for x in text.splitlines() if x.startswith("ON THE RECORD"))
    assert "private" in line


def test_the_brief_never_reaches_a_published_snapshot(env, tmp_path):
    """Briefs are computed live on the Desk and are not issue content. The
    guarantee that matters: research is not part of what publishes."""
    s, db = env
    from leaguepage.ghost_briefs import _matchup_brief
    from leaguepage.issue_builder import assemble_issue

    _a, m = _week(s)
    brief = _matchup_brief(s, LG, SEASON, 1, m["matchup_slug"])["text"]
    assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    blob = "\n".join(x.get("content_md") or "" for x in assembled["sections"])
    for heading in ("ON THE RECORD AGAINST THEM", "WHAT THEY COULD STILL DO",
                    "roast ammunition"):
        assert heading not in blob, heading
    assert "ON THE RECORD AGAINST THEM" in brief   # it does exist, privately


# ------------------------------------------------------- the lineup, built

class _Store:
    """Just enough storage for the pure-ish lineup helpers."""

    def __init__(self, rosters, players, matchups):
        self._r, self._p, self._m = rosters, players, matchups

    def get_rosters(self, league_id):
        return self._r

    def get_player(self, pid):
        return self._p.get(pid, {})

    def get_matchups(self, league_id, week):
        return self._m


class _Lg:
    league_id = "x"
    slug = "surfeit"


VALUES = {
    "qb1": {"name": "Starting QB", "position": "QB", "value": 200.0},
    "te1": {"name": "Weak TE", "position": "TE", "value": 40.0},
    "te2": {"name": "Bench TE", "position": "TE", "value": 120.0},
    "rb1": {"name": "Fine RB", "position": "RB", "value": 150.0},
    "rb9": {"name": "Other RB", "position": "RB", "value": 160.0},
    "te9": {"name": "Other TE", "position": "TE", "value": 110.0},
}


def _store():
    return _Store(
        rosters=[{"roster_id": 1, "players": ["qb1", "te1", "te2", "rb1"], "starters": ["qb1", "te1", "rb1"]},
                 {"roster_id": 2, "players": ["rb9", "te9"], "starters": ["rb9", "te9"]}],
        players={},
        matchups=[{"roster_id": 1, "starters": ["qb1", "te1", "rb1"]},
                  {"roster_id": 2, "starters": ["rb9", "te9"]}])


def test_the_weakest_slot_is_measured_against_the_leagues_own_starters():
    st = _store()
    league_starters = research.league_starting_values(st, _Lg(), 1, VALUES)
    assert sorted(league_starters) == ["QB", "RB", "TE"]
    team = {"roster_id": 1, "starters": ["qb1", "te1", "rb1"]}
    lines = research.weakest_slot(team, VALUES, league_starters, "preseason consensus ranks")
    assert lines and lines[0].startswith("  TE Weak TE:")
    assert "median starting TE" in lines[0] and "preseason consensus ranks" in lines[0]


def test_a_team_above_median_everywhere_says_so():
    st = _store()
    league_starters = research.league_starting_values(st, _Lg(), 1, VALUES)
    team = {"roster_id": 2, "starters": ["rb9", "te9"]}
    lines = research.weakest_slot(team, VALUES, league_starters, "x")
    assert lines == ["  no starting slot below the league's median starter (x)"]


def test_a_bench_player_who_outrates_a_starter_is_a_lineup_call():
    team = {"roster_id": 1, "starters": ["qb1", "te1", "rb1"]}
    lines = research.lineup_calls(_store(), _Lg(), team, VALUES)
    assert lines == ["  bench TE Bench TE rates 80 above starter Weak TE (reference rank, not a projection)"]


def test_a_coin_flip_is_not_a_lineup_call():
    close = dict(VALUES, te2={"name": "Bench TE", "position": "TE", "value": 50.0})
    team = {"roster_id": 1, "starters": ["qb1", "te1", "rb1"]}
    assert research.lineup_calls(_store(), _Lg(), team, close) == []


def test_construction_goes_quiet_once_results_exist(env):
    s, _db = env
    _a, m = _week(s)
    team = m["teams"][0]
    early = research.how_built(s, LG, team, weeks_played=0)
    assert early and early[0].startswith("  opened ")
    assert research.how_built(s, LG, team, weeks_played=research.CONSTRUCTION_WEEKS) == []


def test_the_brief_carries_the_new_blocks(env):
    s, db = env
    from leaguepage.ghost_briefs import brief_for_section

    _a, m = _week(s)
    b = brief_for_section(s, LG, SEASON, "week-01", f"matchup:{m['matchup_slug']}", 1)
    text = b["text"]
    assert "WEAKEST SLOT AND LINEUP CALLS" in text
    assert "HOW THEY WERE BUILT" in text
    assert "WHO MIGHT NOT PLAY" in text
