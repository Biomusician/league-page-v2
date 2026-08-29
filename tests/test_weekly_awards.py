from __future__ import annotations

from leaguepage.weekly_awards import weekly_award_nominations

from fixtures import TEST_LEAGUE, add_players, populate_league, populate_matchups

# starter slot layout from fixtures.league_payload: QB RB RB WR WR TE FLEX
STARTER_POS = ["QB", "RB", "RB", "WR", "WR", "TE", "RB"]


def _team_players(prefix: str, points: list[float]) -> tuple[list[str], dict[str, float]]:
    ids = [f"{prefix}s{i}" for i in range(7)]
    pp = {pid: pts for pid, pts in zip(ids, points)}
    return ids, pp


def _register(storage, prefix: str, bench: dict[str, tuple[str, float, int]] | None = None):
    players = {f"{prefix}s{i}": (f"{prefix.upper()} Starter{i}", STARTER_POS[i], 50 + i)
               for i in range(7)}
    for pid, (pos, _, rank) in (bench or {}).items():
        players[pid] = (f"{prefix.upper()} Bench {pid}", pos, rank)
    add_players(storage, players)


def _basic_week(storage, *, shame=True):
    """10 teams; team 1 loses to team 2 by 3 with a 20-point bench mistake."""
    populate_league(storage, teams=10, rounds=3, picks="complete")
    starters, pp = {}, {}
    scores = {}
    # team 1: 95 points, benched RB with 25 while a starter RB had 5
    ids1, pp1 = _team_players("a", [20, 5, 15, 10, 10, 15, 20])
    _register(storage, "a", bench={"aB1": ("RB", 25.0, 300)})
    pp1["aB1"] = 25.0
    starters[1], pp[1], scores[1] = ids1, pp1, 95.0
    # team 2: 98
    ids2, pp2 = _team_players("b", [20, 14, 14, 14, 12, 12, 12])
    _register(storage, "b")
    starters[2], pp[2], scores[2] = ids2, pp2, 98.0
    # teams 3..10 flat scores, descending
    for rid in range(3, 11):
        idsx, ppx = _team_players(f"t{rid}", [10] * 7)
        _register(storage, f"t{rid}")
        starters[rid], pp[rid] = idsx, ppx
        scores[rid] = 120.0 - rid  # 117 down to 110
    populate_matchups(storage, week=1, teams=10, scores=scores,
                      players_points=pp, starters=starters)
    return scores


def _award(awards, key):
    return next(a for a in awards if a["award_key"] == key)


def test_shame_outcome_changing_case(storage):
    _basic_week(storage)
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    shame = _award(awards, "shame")
    top = shame["nominees"][0]
    assert top["team_slug"] == "team-1"
    assert top["metric_value"] == 20.0
    assert top["outcome_changing"] is True
    assert any("Points sacrificed: 20" in f for f in top["facts"])
    assert shame["slate"] == "strong"


def test_benchwarmer_memorial(storage):
    _basic_week(storage)
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    mem = _award(awards, "benchwarmer-memorial")
    assert mem["nominees"][0]["metric_value"] == 25.0


def test_hard_luck_and_escape(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    # team 1 scores 140 (top) but loses to team 2's 141; team 9 wins with 80 (bottom)
    scores = {1: 140.0, 2: 141.0, 9: 80.0, 10: 79.0}
    for rid in range(3, 9):
        scores[rid] = 100.0 + rid
    populate_matchups(storage, week=1, teams=10, scores=scores)
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    hl = _award(awards, "hard-luck-bastard")
    assert hl["nominees"] and hl["nominees"][0]["team_slug"] == "team-1"
    esc = _award(awards, "escape-artist")
    assert any(n["team_slug"] == "team-9" for n in esc["nominees"])


def test_mercy_rule_blowout(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    scores = {rid: 100.0 for rid in range(1, 11)}
    scores[3], scores[4] = 160.0, 95.0
    populate_matchups(storage, week=1, teams=10, scores=scores)
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    mercy = _award(awards, "mercy-rule")
    assert mercy["nominees"][0]["team_slug"] == "team-3"
    assert mercy["nominees"][0]["metric_value"] == 65.0
    assert mercy["slate"] == "strong"


def test_upset_uses_labeled_preseason_basis_not_projections(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    scores = {rid: 100.0 for rid in range(1, 11)}
    scores[2] = 120.0  # team 2 beats team 1
    populate_matchups(storage, week=1, teams=10, scores=scores)
    ranks = {1: 1, 2: 9}  # commissioner had team 2 ranked 9th, team 1 first
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1, preseason_ranks=ranks)
    upset = _award(awards, "upset-of-the-week")
    assert upset["nominees"]
    assert "Peer and Near-Peer" in upset["nominees"][0]["facts"][0]
    assert "projection" in upset["nominees"][0]["facts"][0]  # states none are fabricated


def test_empty_week_nominates_nothing(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)  # no scores
    assert weekly_award_nominations(storage, TEST_LEAGUE, 1) == []


def test_manager_of_week_not_lazily_highest_score(storage):
    scores = _basic_week(storage)
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    motw = _award(awards, "manager-of-the-week")
    # team 3 has the highest score (117) but no management hooks -> not nominated
    # purely for the score; the metric text says so.
    assert "raw high score alone does not nominate" in motw["metric"]
    for n in motw["nominees"]:
        assert "Won by" in n["facts"][0]


def test_waiver_heist_and_faab_arsonist(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    add_players(storage, {"WV1": ("Waiver Hero", "RB", 200),
                          "WV2": ("Faab Mistake", "WR", 250)})
    ids, pp = _team_players("h", [10] * 7)
    _register(storage, "h")
    ids[6] = "WV1"  # heist player starts in the flex
    pp["WV1"] = 18.0
    pp["WV2"] = 1.0  # arson player rides the bench
    starters = {3: ids}
    players_points = {3: pp}
    scores = {rid: 100.0 for rid in range(1, 11)}
    populate_matchups(storage, week=1, teams=10, scores=scores,
                      players_points=players_points, starters=starters)
    storage.save_transactions(TEST_LEAGUE.league_id, 1, [
        {"transaction_id": "tx1", "type": "waiver", "status": "complete", "leg": 1,
         "adds": {"WV1": 3}, "drops": {}, "waiver_budget": []},
        {"transaction_id": "tx2", "type": "waiver", "status": "complete", "leg": 1,
         "adds": {"WV2": 3}, "drops": {}, "waiver_budget": [{"amount": 40}]},
    ])
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    heist = _award(awards, "waiver-wire-heist")
    assert heist["nominees"][0]["player"] == "Waiver Hero"
    arson = _award(awards, "faab-arsonist")
    assert arson["nominees"][0]["player"] == "Faab Mistake"
    assert arson["nominees"][0]["metric_value"] == 40


def test_galaxy_brain_contrarian_start(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    add_players(storage, {"g0": ("Contrarian Pick", "WR", 400),
                          "gBig": ("Name Brand", "WR", 10)})
    ids, pp = _team_players("g", [10, 10, 10, 10, 10, 10, 10])
    _register(storage, "g")
    ids[3] = "g0"
    pp["g0"] = 24.0
    pp["gBig"] = 4.0  # the name brand sat and flopped
    scores = {rid: 100.0 for rid in range(1, 11)}
    scores[5] = 110.0
    populate_matchups(storage, week=1, teams=10, scores=scores,
                      players_points={5: pp}, starters={5: ids})
    awards = weekly_award_nominations(storage, TEST_LEAGUE, 1)
    gb = _award(awards, "galaxy-brain")
    assert gb["nominees"] and gb["nominees"][0]["player"] == "Contrarian Pick"
    assert "labeled as such" in gb["nominees"][0]["facts"][0]
