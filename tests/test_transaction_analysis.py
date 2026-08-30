"""Transaction rationale engine — synthetic scenarios, no network."""
from __future__ import annotations

import pytest

from leaguepage.config import League
from leaguepage.storage import Storage
from leaguepage.transaction_analysis import (
    analyze_transactions, record_transaction_contexts,
)

from fixtures import add_players, populate_league, populate_matchups

LG = League(slug="tx", display_name="TX", league_id="TXID", theme="disco",
            subtitle="t", adp_source="")


class FakeADP:
    def __init__(self, ranks):
        self.ranks = ranks

    def lookup(self, name, position=None):
        return self.ranks.get(name)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        yield s


def _world(db, rosters: dict[int, list[str]], registry: dict[str, str],
           ranks: dict[str, float]):
    """4-team league; rosters is the POST-transaction state. registry maps
    pid -> position; ranks maps pid (= name here) -> reference rank."""
    populate_league(db, LG, teams=4, rounds=1, picks="none")
    add_players(db, {pid: (pid, pos, 1) for pid, pos in registry.items()})
    db.save_rosters(LG.league_id, [
        {"roster_id": rid, "owner_id": f"u{rid}", "players": pids}
        for rid, pids in rosters.items()])
    return FakeADP(ranks)


def _base_registry():
    reg, ranks = {}, {}
    for rid in range(1, 5):
        for pos, count, base in (("QB", 1, 10), ("RB", 2, 40),
                                 ("WR", 3, 20), ("TE", 1, 30)):
            for i in range(count):
                pid = f"{pos}{rid}{chr(97 + i)}"
                reg[pid] = pos
                ranks[pid] = base + i * 15 + rid * 2
    return reg, ranks


def _rosters_from(reg):
    """Base-registry pids look like WR3b (position, roster id, slot letter);
    later additions (NewTE, WRnew...) are placed explicitly by the test."""
    out: dict[int, list[str]] = {}
    for pid in reg:
        if pid[-2].isdigit():
            out.setdefault(int(pid[-2]), []).append(pid)
    return out


def _txn(rid, adds, drops, *, bid=0, week=1, txn_id="t1", type_="waiver"):
    return {"transaction_id": txn_id, "type": type_, "status": "complete",
            "adds": {p: rid for p in adds}, "drops": {p: rid for p in drops},
            "roster_ids": [rid], "settings": {"waiver_bid": bid},
            "created": 1000 + week}


def test_clear_positional_need_high_confidence(db):
    reg, ranks = _base_registry()
    # team 1's only TE is far below the league; the add is an elite TE
    ranks["TE1a"] = 240
    reg["NewTE"] = "TE"
    ranks["NewTE"] = 10
    rosters = _rosters_from(reg)
    rosters[1].remove("WR1c")            # dropped to make room
    rosters[1].append("NewTE")           # post-state includes the add
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1,
                         [_txn(1, ["NewTE"], ["WR1c"], bid=31)])
    record_transaction_contexts(db, LG, adp=adp)
    rows = analyze_transactions(db, LG, 1, adp=adp)
    assert len(rows) == 1
    r = rows[0]
    assert r["rationale"]["kind"] == "weakness"
    assert r["rationale"]["text"].startswith("Likely rationale")
    assert "aimed at TE" in r["rationale"]["text"]
    assert "31% FAAB" in r["rationale"]["text"]
    assert r["faab"] == 31 and r["significant"]
    assert r.get("rank_shift", "").startswith("TE #4 → #")


def test_questionable_fit_needs_multiple_wrong_signals(db):
    reg, ranks = _base_registry()
    # team 2: league-best WR room, thin RB room; adds a lesser WR and
    # drops its better RB. No injury. Three wrong-direction signals.
    for i, pid in enumerate(("WR2a", "WR2b", "WR2c")):
        ranks[pid] = 3 + i * 3
    ranks["RB2a"], ranks["RB2b"] = 140, 60
    reg["WRnew"] = "WR"
    ranks["WRnew"] = 110
    rosters = _rosters_from(reg)
    rosters[2].remove("RB2b")
    rosters[2].append("WRnew")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [_txn(2, ["WRnew"], ["RB2b"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    r = rows[0]
    assert r["rationale"]["kind"] == "questionable"
    assert "Questionable fit" in r["rationale"]["text"]
    assert r["significant"]
    # critique of the move, not the manager
    assert "manager" not in r["rationale"]["text"].lower()


def test_mixed_evidence_is_not_questionable(db):
    reg, ranks = _base_registry()
    # team 3 has the league's worst RB room; the add is lower-value than
    # the drop, but it fills a severe need — must NOT be questionable.
    ranks["RB3a"], ranks["RB3b"] = 230, 245
    reg["RBnew"] = "RB"
    ranks["RBnew"] = 150
    rosters = _rosters_from(reg)
    rosters[3].remove("WR3c")            # WR3c is a decent receiver
    rosters[3].append("RBnew")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [_txn(3, ["RBnew"], ["WR3c"])])
    record_transaction_contexts(db, LG, adp=adp)
    rows = analyze_transactions(db, LG, 1, adp=adp)
    assert rows[0]["rationale"]["kind"] == "weakness"


def test_streaming_gets_light_touch(db):
    reg, ranks = _base_registry()
    reg["K4a"], reg["Knew"] = "K", "K"
    rosters = _rosters_from(reg)
    rosters[4].remove("K4a") if "K4a" in rosters[4] else None
    rosters[4].append("Knew")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [_txn(4, ["Knew"], ["K4a"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    r = rows[0]
    assert r["rationale"]["kind"] == "streaming"
    assert not r["significant"]
    assert "streaming" in r["rationale"]["text"]


def test_no_obvious_rationale_says_unclear(db):
    reg, ranks = _base_registry()
    # like-for-like WR swap on an average room: no invented motive
    reg["WRswap"] = "WR"
    ranks["WRswap"] = ranks["WR3b"] + 1
    rosters = _rosters_from(reg)
    rosters[3].remove("WR3b")
    rosters[3].append("WRswap")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [_txn(3, ["WRswap"], ["WR3b"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    assert rows[0]["rationale"]["kind"] == "unclear"
    assert rows[0]["rationale"]["text"].startswith("Rationale unclear")


def test_context_recording_is_idempotent_and_guarded(db):
    reg, ranks = _base_registry()
    reg["NewTE"] = "TE"
    ranks["NewTE"] = 55
    rosters = _rosters_from(reg)
    rosters[1].remove("WR1c")
    rosters[1].append("NewTE")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1,
                         [_txn(1, ["NewTE"], ["WR1c"], txn_id="tc1"),
                          # a move the roster no longer reflects: no context
                          _txn(1, ["GhostRB"], [], txn_id="tc2")])
    assert record_transaction_contexts(db, LG, adp=adp) == 1
    assert record_transaction_contexts(db, LG, adp=adp) == 0


def test_outcome_reported_after_games(db):
    reg, ranks = _base_registry()
    reg["NewTE"] = "TE"
    ranks["TE1a"], ranks["NewTE"] = 240, 55
    rosters = _rosters_from(reg)
    rosters[1].remove("WR1c")
    rosters[1].append("NewTE")
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [_txn(1, ["NewTE"], ["WR1c"])])
    populate_matchups(db, LG, week=2, teams=4,
                      scores={1: 101.0, 2: 90.0, 3: 88.0, 4: 70.0},
                      players_points={1: {"NewTE": 14.5}},
                      starters={1: ["NewTE"]})
    rows = analyze_transactions(db, LG, 2, adp=adp)
    assert rows[0]["outcome"] == "Since the move: started 1x for 14.5 points."


def test_trades_carry_facts_not_invented_motive(db):
    reg, ranks = _base_registry()
    rosters = _rosters_from(reg)
    adp = _world(db, rosters, reg, ranks)
    db.save_transactions(LG.league_id, 1, [{
        "transaction_id": "tr1", "type": "trade", "status": "complete",
        "adds": {"WR1a": 2, "WR2a": 1}, "drops": {"WR1a": 1, "WR2a": 2},
        "roster_ids": [1, 2], "settings": {}, "created": 5}])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    r = rows[0]
    assert r["rationale"]["kind"] == "trade"
    assert r["rationale"]["text"] is None
    assert r["significant"]
