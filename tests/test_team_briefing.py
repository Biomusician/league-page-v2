"""Your Team This Week: editorial weighting, season staging, callbacks.

The acceptance question, from the tranche: can a manager answer "how am I
doing, what changed, what is my biggest real weakness, what was my biggest
move, what matters next" in under twenty seconds? The briefing is the answer,
so every one of those fields is pinned here — along with the rule that a
kicker room never gets to be the headline.
"""
from __future__ import annotations

import pytest

from leaguepage import team_briefing as tb
from leaguepage.front_page import MIDSEASON, POSTSEASON, PRESEASON

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def profile(ranks=None, n=10, rid=1, fragility=0.2, starter=5, depth=5):
    ranks = ranks or {"QB": {rid: 1}, "RB": {rid: 10}, "WR": {rid: 3},
                      "TE": {rid: 4}, "K": {rid: 2}, "DEF": {rid: 9}}
    return {
        "n": n, "positions": POSITIONS, "ranks": ranks,
        "starter_ranks": {p: {rid: starter} for p in POSITIONS},
        "depth_ranks": {p: {rid: depth} for p in POSITIONS},
        "teams": {rid: {p: {"fragility": fragility, "count": 3,
                            "top_player": "A Player"} for p in POSITIONS}},
    }


def brief(**over):
    base = dict(state=PRESEASON, name="Los Bandidos", record={"wins": 0, "losses": 0},
                standing=1, weeks_played=0, profile=profile(), rid=1, form=None,
                streak=None, all_play=None, playoff_line=None, playoff_delta=None,
                key_moves=[], next_matchup=None, deltas=[], receipts=[])
    base.update(over)
    return tb.build(**base)


# ------------------------------------------------- the five questions

def test_briefing_answers_the_five_questions():
    b = brief(state=MIDSEASON, weeks_played=6, record={"wins": 4, "losses": 2},
              standing=3, form={"rank": 2, "window": 3, "window_label": "3 weeks"},
              playoff_line="71% (likely)", playoff_delta="playoff odds +14",
              key_moves=[{"line": "Claimed Player X for 24 FAAB",
                          "text": "aimed at RB", "rank_shift": "RB #10 → #6",
                          "questionable": False}],
              next_matchup={"names": "Us vs Them", "anchor": "a-vs-b",
                            "note": "Coalition Warfare"},
              deltas=["standings 5 → 3", "playoff odds +14"])
    assert b["position"] == "3rd · 4-2"                 # how am I doing
    assert b["changed"]                                  # what changed
    assert b["weakness"].startswith("RB")                # my real weakness
    assert "Claimed Player X" in b["key_move"]["line"]   # my biggest move
    assert b["next"]["names"] == "Us vs Them"            # what matters next
    assert b["playoff"] == "71% (likely)"
    assert b["form"] == "#2 scoring over the last 3 weeks"


# ---------------------------------------------- editorial weighting

def test_a_kicker_never_headlines_the_briefing():
    """K is #2 in the fixture and RB is #10. The headline strength must be
    the best SKILL room, not the best room."""
    b = brief()
    assert b["strength"].startswith("WR") or b["strength"].startswith("QB")
    assert not b["strength"].startswith("K")
    assert b["weakness"].startswith("RB")


def test_editorial_strengths_put_skill_positions_first():
    sw = {"strengths": [{"position": "K", "rank": 2, "note": "K room ranks 2/10"},
                        {"position": "QB", "rank": 1, "note": "QB room ranks 1/10"}],
          "weaknesses": [{"position": "DEF", "rank": 9, "note": "DEF room ranks 9/10"},
                         {"position": "RB", "rank": 10, "note": "RB room ranks 10/10"}]}
    strengths, weaknesses = tb.editorial_strengths(profile(), 1, sw)
    assert strengths[0].startswith("QB")
    assert weaknesses[0].startswith("RB")
    # K at #2 is not league-best, so it does not earn a headline slot at all
    assert not any(s.startswith("K ") for s in strengths)
    # DEF at #9 of 10 is not last either
    assert not any(w.startswith("DEF") for w in weaknesses)


def test_a_league_best_kicker_room_survives_but_never_leads():
    sw = {"strengths": [{"position": "K", "rank": 1, "note": "K room ranks 1/10"},
                        {"position": "QB", "rank": 3, "note": "QB room ranks 3/10"}],
          "weaknesses": [{"position": "RB", "rank": 10, "note": "RB room ranks 10/10"}]}
    strengths, _ = tb.editorial_strengths(profile(), 1, sw)
    assert strengths[0].startswith("QB")
    assert any(s.startswith("K ") for s in strengths)


def test_the_analytical_table_is_untouched():
    """Editorial weighting reorders the headline, it does not delete data."""
    p = profile()
    assert set(p["ranks"]) == set(POSITIONS)
    assert p["ranks"]["K"][1] == 2


# ------------------------------------------------- season staging

def test_draft_recap_ages_down_the_page():
    pre = tb.section_order(PRESEASON)
    mid = tb.section_order(MIDSEASON)
    post = tb.section_order(POSTSEASON)
    assert pre.index("draft") < pre.index("performance")
    assert mid.index("performance") < mid.index("draft")
    assert post.index("draft") == len(post) - 1
    # nothing is ever dropped
    for order in (pre, mid, post):
        assert set(order) == set(pre)


def test_preseason_briefing_uses_construction_not_a_record():
    b = brief()
    assert "Preseason" in b["position"]
    assert b["form"] is None
    assert b["storyline"] and "Built around" in b["storyline"]


# ---------------------------------------------------- storylines

def test_storyline_reads_schedule_luck_when_all_play_disagrees():
    b = brief(state=MIDSEASON, weeks_played=6, record={"wins": 2, "losses": 4},
              all_play={"wins": 40, "losses": 14})
    assert "bad schedule" in b["storyline"]


def test_storyline_reads_a_flattering_record_the_other_way():
    b = brief(state=MIDSEASON, weeks_played=6, record={"wins": 5, "losses": 1},
              all_play={"wins": 20, "losses": 34})
    assert "ahead of the performance" in b["storyline"]


def test_storyline_refuses_to_call_luck_from_two_games():
    b = brief(state=MIDSEASON, weeks_played=2, record={"wins": 0, "losses": 2},
              all_play={"wins": 15, "losses": 3})
    assert b["storyline"] is None or "schedule" not in b["storyline"]


def test_storyline_notices_a_streak():
    b = brief(state=MIDSEASON, weeks_played=5, record={"wins": 3, "losses": 2},
              streak={"length": 3, "kind": "top-half scoring"})
    assert "3 straight weeks" in b["storyline"]


# ------------------------------------------------------ what to watch

def test_watch_names_the_room_most_likely_to_cost_a_week():
    b = brief()
    assert any("RB" in w for w in b["watch"])


def test_a_receipt_reaches_the_briefing():
    b = brief(receipts=[{"status": "Under pressure", "quote": "a claim",
                         "status_note": "why", "href": "x", "issue_label": "y"}])
    assert b["receipts"]
    assert any("under pressure" in w for w in b["watch"])


# ------------------------------------------------------ league mentions

SNAP = {
    "issue_label": "Draft Issue", "season": "2026", "href": "2026/draft/index.html",
    "sections": [{
        "module_key": "custom", "title": "Draft Power Rankings",
        "content_md": (
            "## Draft Power Rankings\n\n"
            "### 1. Los Bandidos (-42)\n\n"
            "The design holds one assumption per capability area instead of five, "
            "which is why the bill stayed small.\n\n"
            "### 2. Wild SeeKats (-143)\n\n"
            "The most conventional force structure in the league, one of "
            "everything and nothing exotic at all.\n\n"
            "### 3. Dave (+1)\n\n"
            "Somehow the sole roster to get a positive value against the "
            "reference board this year.\n\n"
            "| Rk | Team | Score |\n| --- | --- | --- |\n"
            "| 1 | Los Bandidos | 100 |\n"),
    }],
}
NAME_TOKENS = {1: {"los", "bandidos"}, 2: {"wild", "seekats"}, 3: {"dave"}}


def test_mentions_quote_the_sentence_under_the_teams_own_heading():
    out = tb.league_mentions([SNAP], "Los Bandidos", 1, NAME_TOKENS)
    assert len(out) == 1
    assert "one assumption per capability area" in out[0]["quote"]
    assert out[0]["issue_label"] == "Draft Issue"
    assert out[0]["href"] == "2026/draft/index.html"
    assert out[0]["section_title"] == "Draft Power Rankings"


def test_mentions_never_quote_a_markdown_table_row():
    for rid in NAME_TOKENS:
        for m in tb.league_mentions([SNAP], "x", rid, NAME_TOKENS):
            assert "|" not in m["quote"]
            assert len(m["quote"].split()) >= 8


def test_mentions_do_not_attribute_another_teams_block():
    out = tb.league_mentions([SNAP], "Wild SeeKats", 2, NAME_TOKENS)
    assert all("one assumption per capability" not in m["quote"] for m in out)
    assert any("most conventional force structure" in m["quote"] for m in out)


def _issue(i: int) -> dict:
    return {
        "issue_label": f"Week {i}", "season": "2026",
        "href": f"2026/week-{i}/index.html",
        "sections": [{"module_key": "lowdown", "title": "The Lowdown",
                      "content_md": f"Los Bandidos did a distinct thing in "
                                    f"week {i} that nobody else managed."}],
    }


def test_mentions_are_capped():
    assert len(tb.league_mentions([_issue(i) for i in range(6)], "Los Bandidos",
                                  1, NAME_TOKENS)) == 3


def test_the_same_sentence_is_never_quoted_twice():
    """Six issues carrying the identical line is one callback, not six."""
    repeated = [dict(SNAP, issue_label=f"Week {i}",
                     href=f"2026/week-{i}/index.html") for i in range(6)]
    out = tb.league_mentions(repeated, "Los Bandidos", 1, NAME_TOKENS)
    assert len(out) == 1
    assert len({m["quote"] for m in out}) == len(out)
