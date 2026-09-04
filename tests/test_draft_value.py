"""Draft-market value classification (REACH/STEAL) — spec cases."""
from __future__ import annotations

from leaguepage.draft_value import (
    CAP_ROUNDS, classify_pick, consensus_style, team_draft_profile,
)


# delta convention: pick_no - reference rank; negative = taken early.

def _cls(delta, size):
    return classify_pick(delta, size)["draft_value_class"]


def test_10_team_thresholds():
    assert _cls(-9, 10) == "EARLY"        # 9 early: not a reach
    assert _cls(-10, 10) == "REACH"       # exactly one full round
    assert _cls(-11, 10) == "REACH"
    assert _cls(9, 10) == "VALUE"         # 9 late: not a steal
    assert _cls(10, 10) == "STEAL"


def test_12_team_thresholds():
    assert _cls(-11, 12) == "EARLY"
    assert _cls(-12, 12) == "REACH"
    assert _cls(11, 12) == "VALUE"
    assert _cls(12, 12) == "STEAL"


def test_exact_match_and_tolerance():
    assert _cls(0, 12) == "ON BOARD"
    assert _cls(2, 12) == "ON BOARD"
    assert _cls(-2, 12) == "ON BOARD"
    assert _cls(3, 12) == "VALUE"


def test_missing_reference_rank():
    assert classify_pick(None, 12) is None
    assert classify_pick(5, 0) is None


def test_semantic_fields_never_need_sign_reasoning():
    d = classify_pick(-16, 12)
    assert d["picks_early"] == 16 and d["picks_late"] == 0
    assert d["rounds_early"] == 1.33
    assert d["label"] == "REACH · 16 picks early"
    s = classify_pick(18, 12)
    assert s["picks_late"] == 18 and s["picks_early"] == 0
    assert s["label"] == "STEAL · 18 picks late"


def test_extreme_reach_is_reach_with_capped_intensity():
    # the known Disco extreme: ~244 picks ahead of reference
    d = classify_pick(-244, 12)
    assert d["draft_value_class"] == "REACH"
    assert d["intensity"] == 1.0            # visual cap
    assert d["picks_early"] == 244          # numeric delta uncapped
    assert d["sort_value"] == -244


def test_intensity_normalizes_by_league_rounds():
    # one full round is the same intensity in a 10- and 12-team league
    ten = classify_pick(-10, 10)["intensity"]
    twelve = classify_pick(-12, 12)["intensity"]
    assert ten == twelve == round(1 / CAP_ROUNDS, 2)


def test_team_profile_counts_and_style():
    def team(deltas, position="RB"):
        return {"picks_by_round": [{"delta": d, "position": position}
                                   for d in deltas],
                "biggest_reach": None, "biggest_value": None}
    profs = {
        1: team_draft_profile(team([-15, -12, -14, 20]), 12),   # defier
        2: team_draft_profile(team([0, 1, -1, 2]), 12),         # follower
        3: team_draft_profile(team([-5, 4, 6, -3]), 12),
        4: team_draft_profile(team([-6, 5, 4, -4]), 12),
    }
    assert profs[1]["reach_picks"] == 3 and profs[1]["steal_picks"] == 1
    styles = consensus_style(profs)
    assert styles[1] == "consensus-defying"
    assert styles[2] == "consensus-following"


def test_draft_style_ignores_the_kicker_tax():
    """How a manager drafts is a claim about judgment. Every reference board
    ranks kickers and defenses below the draftable range while the lineup
    forces everybody to draft them, so folding those deltas in measures the
    board's shape and calls it a personality."""
    from leaguepage.draft_value import team_draft_profile

    picks = ([{"delta": 0, "position": "RB"}] * 4
             + [{"delta": -95, "position": "K"},
                {"delta": -130, "position": "DEF"}])
    prof = team_draft_profile(
        {"picks_by_round": picks, "biggest_reach": None, "biggest_value": None}, 12)
    assert prof["rated_picks"] == 4
    assert prof["reach_picks"] == 0
    assert prof["mean_abs_rounds"] == 0.0
