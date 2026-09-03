"""Front page: season-state adaptation and editorial hierarchy.

The bug this file exists to prevent is the one that shipped: a homepage
whose second module was a table of 0-0 / 0.0 PF in August, and whose modules
all carried the same weight in December.
"""
from __future__ import annotations

import pytest

from leaguepage import front_page as fp

NAMES = {1: "Statistical Anomalies", 2: "Los Bandidos", 3: "Wild SeeKats",
         4: "Dave", 5: "Gary"}
SLUGS = {rid: f"team-{rid}" for rid in NAMES}


def profile(n=5, ranks=None):
    positions = ["QB", "RB", "WR", "TE", "K", "DEF"]
    ranks = ranks or {p: {rid: ((rid + i) % n) + 1 for rid in NAMES}
                      for i, p in enumerate(positions)}
    return {"n": n, "positions": positions, "ranks": ranks,
            "teams": {rid: {} for rid in NAMES}}


def ctx(**over):
    base = {
        "week": 1, "weeks_played": 0, "playoff_week_start": 15,
        "last_sync": "2026-08-30T23:01:08-04:00",
        "names": NAMES, "slugs": SLUGS, "cards": [], "moves": [],
        "profile": profile(), "form": {}, "movers": [], "hot": [], "trouble": [],
        "playoff": None, "reaches": [], "steals": [], "receipt": None,
    }
    base.update(over)
    return base


def card(anchor="a-vs-b", names="A vs B", tags=(), prominence="FEATURE", score=None):
    return {"anchor": anchor, "names": names, "records": "0-0 vs 0-0",
            "score": score, "tags": list(tags), "prominence": prominence,
            "preview_html": None}


def move(team="Los Bandidos", questionable=False, priority=5):
    return {"team": team, "line": "Added X · dropped Y", "questionable": questionable,
            "priority": priority, "text": "Because of a thing."}


def kinds(front):
    return [i["kind"] for i in front["briefing"]]


# ------------------------------------------------------------ season state

@pytest.mark.parametrize("played,week,expected", [
    (0, 1, fp.PRESEASON),
    (0, 6, fp.PRESEASON),        # the counter moved; no games did
    (1, 2, fp.OPENING),
    (2, 3, fp.OPENING),
    (4, 5, fp.MIDSEASON),
    (10, 11, fp.PLAYOFF_RACE),
    (13, 15, fp.POSTSEASON),
])
def test_season_state(played, week, expected):
    assert fp.season_state(played, week, 15) == expected


def test_freshness_tells_a_visitor_what_the_site_knows():
    assert "no games played" in fp.freshness_line(fp.PRESEASON, 0, 1, None)
    assert fp.freshness_line(fp.MIDSEASON, 4, 5, None) == "Updated after Week 4"
    assert "synced 2026-08-30" in fp.freshness_line(fp.MIDSEASON, 4, 5,
                                                    "2026-08-30T23:01:08-04:00")


# ------------------------------------------------------------- preseason

def test_preseason_suppresses_the_zero_zero_table():
    front = fp.build(ctx())
    assert front["show_standings"] is False
    assert front["standings_note"]
    assert front["rooms"], "preseason needs something in the standings slot"


def test_preseason_rooms_are_skill_positions_only():
    front = fp.build(ctx())
    assert [r["pos"] for r in front["rooms"]] == ["QB", "RB", "WR", "TE"]


def test_preseason_still_finds_at_least_three_real_items():
    front = fp.build(ctx(
        cards=[card(tags=["Coalition Warfare"])],
        moves=[move(questionable=True)],
        reaches=[{"name": "Jordan James", "team": "L'entente",
                  "dv": {"label": "REACH · 131 picks early"}}]))
    assert len(front["briefing"]) >= 3
    assert "game" in kinds(front) and "move" in kinds(front) and "draft" in kinds(front)


def test_a_questionable_move_outranks_a_preseason_opener():
    front = fp.build(ctx(cards=[card()], moves=[move(questionable=True)]))
    assert front["lead"]["kind"] == "move"
    assert "QUESTIONABLE MOVE" in front["lead"]["tags"]


def test_briefing_is_suppressed_rather_than_padded():
    front = fp.build(ctx(profile=None))
    assert front["briefing"] == []
    assert front["lead"] is None


# ------------------------------------------------------------- in season

def test_standings_appear_once_games_exist():
    front = fp.build(ctx(weeks_played=4, week=5))
    assert front["show_standings"] is True
    assert front["rooms"] == []
    assert front["freshness"] == "Updated after Week 4 · synced 2026-08-30"


def test_midseason_promotes_results_over_draft_material():
    front = fp.build(ctx(
        weeks_played=6, week=7, cards=[card(score="120.4 – 98.1")],
        movers=["Los Bandidos: standings 7 → 3"],
        reaches=[{"name": "X", "team": "Y", "dv": {"label": "REACH · 40 picks early"}}]))
    assert "draft" not in kinds(front), "draft material is preseason furniture"
    assert "standings" in kinds(front)
    assert front["lead"]["kind"] in ("standings", "game")


def test_playoff_race_leads_with_the_cutline():
    front = fp.build(ctx(
        weeks_played=11, week=12, cards=[card()],
        movers=["Dave: standings 5 → 4"],
        playoff={"spots": 2, "stage": "percentages",
                 "rows": [{"name": "Los Bandidos", "odds": "88%", "band": "likely"},
                          {"name": "Dave", "odds": "54%", "band": "live"},
                          {"name": "Gary", "odds": "31%", "band": "long shot"}]}))
    assert front["lead"]["kind"] == "playoff"
    assert "Dave" in front["lead"]["headline"] and "Gary" in front["lead"]["headline"]


def test_cutline_is_silent_before_the_race():
    front = fp.build(ctx(weeks_played=4, week=5, playoff={
        "spots": 2, "rows": [{"name": "a", "band": "x"}, {"name": "b", "band": "y"},
                             {"name": "c", "band": "z"}]}))
    assert "playoff" not in kinds(front)


# ---------------------------------------------------------- author rule

def test_the_author_is_never_the_team_to_watch():
    ranks = {p: {rid: 1 if rid == 1 else 5 for rid in NAMES}
             for p in ["QB", "RB", "WR", "TE", "K", "DEF"]}
    ranks["TE"] = {rid: 5 if rid == 1 else 1 for rid in NAMES}
    front = fp.build(ctx(profile=profile(ranks=ranks), author_roster_id=1))
    watch = [i for i in front["briefing"] if i["kind"] == "watch"]
    assert all("Statistical Anomalies" not in i["headline"] for i in watch)


# ------------------------------------------------------------ link depth

def test_every_item_leads_somewhere():
    front = fp.build(ctx(
        weeks_played=6, week=7, cards=[card()], moves=[move()],
        movers=["Dave: standings 5 → 4"], hot=["Gary: #1 scoring"],
        receipt={"claim": "“a claim”", "status": "Under pressure",
                 "status_note": "why", "href": "2026/draft/index.html"}))
    assert front["briefing"]
    for item in front["briefing"]:
        assert item["href"] and not item["href"].startswith("/")
        assert item["cta"]


def test_item_count_is_capped():
    front = fp.build(ctx(
        weeks_played=6, week=7, cards=[card()], moves=[move(questionable=True)],
        movers=["Dave: standings 5 → 4"], hot=["Gary: #1 scoring"],
        trouble=["Dave: #5 of 5"], form={2: {"rank": 1, "window": "3 weeks"}},
        receipt={"claim": "“a claim”", "status": "Aging well",
                 "status_note": "why", "href": "2026/draft/index.html"}))
    assert len(front["briefing"]) == fp.MAX_ITEMS
    assert len(set(kinds(front))) == len(kinds(front)), "one item per kind"
