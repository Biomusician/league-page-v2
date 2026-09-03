"""The ranker's pathological cases, pinned.

Every one of these was a real ordering the model produced: a genuine event
filed below a trivial one, or a whole half of the inbox scoring identically
and sorting alphabetically.
"""
from __future__ import annotations

from leaguepage import significance as sig
from leaguepage.change_inbox import _candidate_magnitude, _matters


def _score(item, ctx=None):
    return sig.score_item(item, ctx or {})


UPSET = {"item_id": "change:upset:3:5", "category": "result",
         "magnitude": 3 / 11 + 0.25, "consequence": 0.35 + 0.65 * (1 - 2 / 11),
         "rarity": 0.3 + 3 / 11, "expectation": 3 / 11, "fresh": True}
BENCH_TRADE = {"item_id": "change:txn:9", "category": "transaction",
               "magnitude": 0.30 + 0.08 * 2, "cost": 0.0, "fresh": True}


def test_a_real_upset_outranks_a_bench_piece_trade():
    """A nine-seed beating a three-seed scored 38 and was filed Minor, while
    two teams swapping bench pieces for no FAAB scored 40."""
    upset, trade = _score(UPSET), _score(BENCH_TRADE)
    assert upset["score"] > trade["score"] + 20, (upset, trade)
    assert upset["band"] in ("Strong", "Lead story")
    assert trade["band"] == "Minor"


def test_a_free_trade_is_not_charged_as_a_cost():
    """Magnitude already counts the players. Counting them again as cost
    scored the same fact twice."""
    assert not _score(BENCH_TRADE).get("components") or all(
        "cost" not in c["label"] for c in _score(BENCH_TRADE)["components"])


def test_a_kicker_room_does_not_outrank_a_playoff_swing():
    kicker = {"item_id": "change:pos:K:4", "category": "strength",
              "magnitude": 3 / 11, "consequence": 0.15, "fresh": True}
    odds = {"item_id": "change:playoff:4", "category": "playoff",
            "magnitude": 0.30 / 0.50, "consequence": 0.30 / 0.30, "fresh": True}
    assert _score(odds)["score"] > _score(kicker)["score"]


def test_the_top_band_is_reachable_without_a_clinch():
    """Only a clinch or a maximal upset could clear 80, so the Lead story
    band was dead for the first ten weeks of every season."""
    big = {"item_id": "change:upset:9:2", "category": "result",
           "magnitude": 1.0, "consequence": 0.95, "rarity": 0.9,
           "expectation": 0.9, "fresh": True}
    assert _score(big)["band"] == "Lead story"


def test_candidates_no_longer_all_score_the_same():
    """Every non-matchup candidate arrived at a flat 0.4 with no other
    signal, so they all scored exactly 16 and sorted by id."""
    seen = set()
    for cand in (
        {"category": "record", "facts": ["a", "b", "c"], "confidence": "weeks 1-5"},
        {"category": "track", "facts": ["a"]},
        {"category": "force-flow", "facts": ["a", "b"], "players": ["X"]},
        {"category": "coalition", "facts": []},
    ):
        mag, label, extra = _candidate_magnitude(cand)
        item = {"item_id": f"story:{cand['category']}:x", "category": "story",
                "magnitude": mag, **extra}
        seen.add(_score(item)["score"])
        assert label
    assert len(seen) == 4, seen


def test_an_item_says_why_it_matters_not_just_why_it_scored():
    """`explain` answers why the ranker ranked it. '+12 standings or playoff
    consequence (50%)' is not a reason a reader would care."""
    assert "December" in _matters({"category": "playoff"})
    assert _matters({"category": "result",
                     "consequence_label": "the 2nd seed lost"}).endswith(
        "The 2nd seed lost.")
    assert _matters({"category": "nonsense"}) == ""


# --------------------------------------- triage that reaches the issue

def test_a_triaged_change_item_reaches_the_briefs():
    """The inbox offered "Add to Issue" on every item and the decision
    reached nothing for a change:* id: the builders iterate the story
    candidate list and a diff item was never in it."""
    from leaguepage.change_inbox import as_candidates

    items = [{
        "item_id": "change:upset:3:5", "category": "result",
        "headline": "Upset: Alpha beat Bravo",
        "what_changed": "Alpha was 9th going in and won by 30",
        "before": "Alpha 9th, Bravo 2nd", "after": "Alpha 130, Bravo 100",
        "facts": ["Week 3: 130 to 100."], "teams": ["Alpha", "Bravo"],
        "evidence": ["sleeper:matchup:L:3:5"], "sections": ["lowdown", "ctp"],
        "matters": "Results are the only thing the standings are made of.",
        "significance": {"band": "Strong"},
    }]
    out = as_candidates(items)
    assert len(out) == 1
    c = out[0]
    assert c["candidate_id"] == "change:upset:3:5"
    assert c["headline"] == "Upset: Alpha beat Bravo"
    assert c["recommended_sections"] == ["lowdown", "ctp"]
    assert c["evidence"] == ["sleeper:matchup:L:3:5"]
    # the before/after is the most useful fact the diff carries, and the
    # candidate shape has nowhere else to put it
    assert c["facts"][0] == "Alpha 9th, Bravo 2nd -> Alpha 130, Bravo 100"
    assert c["why"] == "Results are the only thing the standings are made of."


def test_a_story_routed_to_the_lowdown_now_produces_a_brief():
    """"lowdown" mapped to None, so deliberately routing a story there
    produced no brief at all."""
    import inspect

    from leaguepage import issue_builder
    src = inspect.getsource(issue_builder.build_section_authoring)
    assert '"lowdown": None' not in src
    assert '"lowdown": "lowdown"' in src
