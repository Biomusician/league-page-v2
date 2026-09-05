"""A reference rank past the end of the draft is not a draft position.

The board ranks every player it knows; the draft only has so many picks.
When a player's rank falls past the last pick there was no slot he could
have been taken from, so the deviation measures where the board stops, not
what the manager did. The site was printing "REACH · 244 picks early" in a
228-pick draft.

Live numbers when this was written: 10 of Disco's 228 picks (4%) and 24 of
The Surfeit's 150 (16%) sit off the board, and 19 of Surfeit's 24 are
kickers and defenses — which is the same calibration story the rest of the
codebase already refuses to publish.
"""
from __future__ import annotations

from leaguepage.draft_value import (CLASS_OFF_BOARD, CLASS_REACH,
                                    classify_pick, team_draft_profile)


def test_an_off_board_pick_carries_no_magnitude():
    on = classify_pick(-244.0, 12)
    off = classify_pick(-244.0, 12, off_board=True)
    assert on["draft_value_class"] == CLASS_REACH
    assert "244" in on["label"]
    assert off["draft_value_class"] == CLASS_OFF_BOARD
    assert "244" not in off["label"]
    assert off["label"] == "outside the reference board's range"
    assert off["picks_early"] == 0.0 and off["intensity"] == 0.0


def test_a_pick_inside_the_board_is_unchanged():
    """The guard must not quietly swallow real reaches."""
    d = classify_pick(-30.0, 12)
    assert d["draft_value_class"] == CLASS_REACH
    assert d["label"] == "REACH · 30 picks early"


def test_the_draft_style_label_ignores_off_board_picks():
    """`consensus-defying` is a claim about how somebody drafts. Averaging
    in deviations the board cannot support describes the board."""
    picks = ([{"delta": 2.0, "position": "RB"}] * 4
             + [{"delta": -244.0, "position": "WR", "off_board": True}])
    prof = team_draft_profile(
        {"picks_by_round": picks, "biggest_reach": None, "biggest_value": None}, 12)
    assert prof["rated_picks"] == 4
    assert prof["reach_picks"] == 0
    assert prof["mean_abs_rounds"] == round(2.0 / 12, 2)


def test_off_board_survives_the_team_summary_whitelist():
    """`summarize_team` copies a fixed set of fields into picks_by_round.
    Dropping this one there put the magnitude back on the page."""
    from leaguepage.config import get_league
    from leaguepage.draft_analysis import summarize_team

    picks = [{"round": 1, "pick_no": 1, "name": "A", "position": "K",
              "nfl_team": "AAA", "adp": 900.0, "delta": -899.0,
              "adp_source": "ref", "off_board": True, "roster_id": 1,
              "player_id": "p1", "evidence": [], "draft_slot": 1}]
    out = summarize_team(get_league("disco"), "2026",
                         {"roster_id": 1, "team_name": "T", "team_slug": "t",
                          "display_names": [], "manager_keys": [],
                          "co_managed": False, "evidence": []},
                         picks, starters_count=9, rounds=1)
    assert out["picks_by_round"][0]["off_board"] is True


def test_headline_reaches_never_list_an_off_board_pick():
    """The Draft page showed one under Biggest Reaches, wearing the verdict
    "outside the reference board's range" -- a reach with no magnitude,
    ranked among the biggest. Off-board picks stay out of both lists."""
    from leaguepage.draft_value import headline_deviations

    picks = [{"name": "In range", "position": "WR", "delta": -30.0, "pick_no": 40,
              "adp": 70.0, "team_slug": "t", "off_board": False},
             {"name": "Off board", "position": "WR", "delta": -244.0, "pick_no": 41,
              "adp": 285.0, "team_slug": "t", "off_board": True}]
    out = headline_deviations(picks, 12)
    assert [p["name"] for p in out["skill_reaches"]] == ["In range"]
    assert out["skill_steals"] == [] and out["special_teams"] == []

