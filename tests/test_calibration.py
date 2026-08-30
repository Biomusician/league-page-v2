"""Analytical calibration guards: K/DST headline handling, superflex
reference selection, draft-reference immutability, usable-depth surplus,
questionable-move threshold, move descriptions, editorial priority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from leaguepage.config import LEAGUES, REPO_ROOT, League
from leaguepage.draft_value import headline_deviations, position_order_context
from leaguepage.storage import Storage
from leaguepage.transaction_analysis import (
    _priority, analyze_transactions, describe_move,
)

from fixtures import add_players, populate_league

LG = League(slug="cal", display_name="CAL", league_id="CALID", theme="disco",
            subtitle="t", adp_source="")


# ------------------------------------------------ draft reference boards

def _league_by_slug(slug):
    return next(lg for lg in LEAGUES if lg.slug == slug)


def test_superflex_league_uses_superflex_board():
    disco = _league_by_slug("disco")
    assert disco.adp_source == "fantasypros_ecr_redraft_superflex"
    snap = json.loads((REPO_ROOT / "refdata" / "adp"
                       / f"{disco.adp_source}.json").read_text(encoding="utf-8"))
    assert snap["scoring_format"] == "half_ppr_superflex"
    # superflex-elevated QBs: consensus #1 overall is a QB on this board
    top = min(snap["players"], key=lambda p: p["rank"])
    assert top["position"] == "QB"


def test_one_qb_league_uses_one_qb_board():
    surfeit = _league_by_slug("surfeit")
    assert surfeit.adp_source == "fantasypros_ecr_redraft_half_ppr"
    snap = json.loads((REPO_ROOT / "refdata" / "adp"
                       / f"{surfeit.adp_source}.json").read_text(encoding="utf-8"))
    assert snap["scoring_format"] == "half_ppr_1qb"


def test_draft_reference_snapshots_are_immutable():
    """Reach/Steal history must never drift because rankings updated.
    These hashes pin the exact boards the 2026 drafts were measured
    against; changing either file is a conscious decision that must come
    with a story for historical integrity (e.g. a new season's snapshot
    under a NEW source key), not a silent re-import."""
    pins = {
        "fantasypros_ecr_redraft_superflex.json":
            "eab5a450cad1c4f7006783c7a8ec75e23540c715a7c404ee32be7db77a8051ee",
        "fantasypros_ecr_redraft_half_ppr.json":
            "1814f5d18e08515cd969eaf1f22000ac4dde792151beef4c3b66f659ecbc77a1",
    }
    for fname, expected in pins.items():
        digest = hashlib.sha256(
            (REPO_ROOT / "refdata" / "adp" / fname).read_bytes()).hexdigest()
        assert digest == expected, f"{fname} changed since the 2026 draft"


# --------------------------------------------------- headline treatment

def _pick(name, pos, pick_no, delta):
    return {"name": name, "position": pos, "pick_no": pick_no,
            "delta": delta, "adp": pick_no - delta, "team_slug": "t"}


def test_headline_excludes_special_teams_but_keeps_all_rows():
    picks = [
        _pick("Skill Reach", "RB", 20, -30),
        _pick("Mandatory Kicker", "K", 103, -130),
        _pick("Mandatory DST", "DEF", 104, -128),
        _pick("Skill Steal", "WR", 90, 25),
        _pick("Mild K", "K", 140, -12),      # < 2 rounds: not an outlier
    ]
    hd = headline_deviations(picks, 10)
    assert [p["name"] for p in hd["skill_reaches"]] == ["Skill Reach"]
    assert [p["name"] for p in hd["skill_steals"]] == ["Skill Steal"]
    assert [p["name"] for p in hd["special_teams"]] == [
        "Mandatory Kicker", "Mandatory DST"]
    # input list untouched: the full board still carries every pick
    assert len(picks) == 5


def test_position_order_context_uses_within_position_scale():
    class FakeSource:
        players = [{"name": "Best K", "position": "K", "rank": 202},
                   {"name": "Mid K", "position": "K", "rank": 219},
                   {"name": "Deep K", "position": "K", "rank": 260}]
    picks = [_pick("Mid K", "K", 124, -95), _pick("Best K", "K", 133, -69)]
    ctx = position_order_context(FakeSource(), picks, picks[0])
    assert ctx == "1st K drafted · consensus K2"


# ------------------------------------------- transaction calibration

class FakeADP:
    def __init__(self, ranks):
        self.ranks = ranks

    def lookup(self, name, position=None):
        return self.ranks.get(name)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        yield s


def _base(db):
    reg, ranks = {}, {}
    for rid in range(1, 5):
        for pos, count, base in (("QB", 1, 10), ("RB", 2, 40),
                                 ("WR", 3, 20), ("TE", 1, 30)):
            for i in range(count):
                pid = f"{pos}{rid}{chr(97 + i)}"
                reg[pid] = pos
                ranks[pid] = base + i * 15 + rid * 2
    return reg, ranks


def _setup(db, reg, ranks, rosters):
    populate_league(db, LG, teams=4, rounds=1, picks="none")
    add_players(db, {pid: (pid, pos, 1) for pid, pos in reg.items()})
    db.save_rosters(LG.league_id, [
        {"roster_id": rid, "owner_id": f"u{rid}", "players": pids}
        for rid, pids in rosters.items()])
    return FakeADP(ranks)


def _rosters(reg):
    out = {}
    for pid in reg:
        if pid[-2].isdigit():
            out.setdefault(int(pid[-2]), []).append(pid)
    return out


def _txn(rid, adds, drops, *, bid=0, txn_id="t1"):
    return {"transaction_id": txn_id, "type": "waiver", "status": "complete",
            "adds": {p: rid for p in adds}, "drops": {p: rid for p in drops},
            "roster_ids": [rid], "settings": {"waiver_bid": bid},
            "created": 1}


def test_raw_count_does_not_imply_surplus(db):
    """Two studs + fringe bodies is not the deepest room: the drop of a
    fringe receiver from that roster earns no surplus story."""
    reg, ranks = _base(db)
    # team 1: elite WR1a/WR1b, then four fringe receivers (no usable depth)
    ranks["WR1a"], ranks["WR1b"], ranks["WR1c"] = 3, 6, 260
    for extra in ("WR1d", "WR1e", "WR1f"):
        reg[extra] = "WR"
        ranks[extra] = 280
    reg["RBnew"] = "RB"
    ranks["RBnew"] = 90    # decent add, but RB room is mid, not bottom
    rosters = _rosters(reg)
    rosters[1] += ["WR1d", "WR1e", "RBnew"]   # post state (WR1f dropped)
    adp = _setup(db, reg, ranks, rosters)
    db.save_transactions(LG.league_id, 1, [_txn(1, ["RBnew"], ["WR1f"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    text = rows[0]["rationale"]["text"] or ""
    assert "surplus" not in text.lower()
    assert "players rostered" not in text


def test_good_position_plus_lower_value_is_not_questionable(db):
    """Adding to an already-good room with a lower-consensus player is a
    speculative thesis the model cannot see — 'unclear', not questionable,
    when the drop side is unremarkable."""
    reg, ranks = _base(db)
    for i, pid in enumerate(("WR2a", "WR2b", "WR2c")):
        ranks[pid] = 3 + i * 3          # team 2 = best WR room
    reg["WRnew"] = "WR"
    ranks["WRnew"] = 150                # lower value than the drop
    rosters = _rosters(reg)
    rosters[2].remove("WR2c")           # drop from their STRONG room (mid value)
    rosters[2].append("WRnew")
    adp = _setup(db, reg, ranks, rosters)
    db.save_transactions(LG.league_id, 1, [_txn(2, ["WRnew"], ["WR2c"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    assert rows[0]["rationale"]["kind"] != "questionable"


def test_medium_confidence_wording(db):
    """Medium-confidence stories say 'One plausible rationale', never the
    high-confidence 'Likely rationale'."""
    reg, ranks = _base(db)
    # rebalance shape: strong RB depth feeding a weak TE room
    ranks["TE3a"] = 240
    reg["TEnew"] = "TE"
    ranks["TEnew"] = 120                # helps, but not elite: medium story
    rosters = _rosters(reg)
    rosters[3].remove("WR3c")
    rosters[3].append("TEnew")
    adp = _setup(db, reg, ranks, rosters)
    db.save_transactions(LG.league_id, 1, [_txn(3, ["TEnew"], ["WR3c"])])
    rows = analyze_transactions(db, LG, 1, adp=adp)
    r = rows[0]["rationale"]
    if r["confidence"] == "medium":
        assert r["text"].startswith("One plausible rationale")
    else:
        assert r["confidence"] == "high" and \
            r["text"].startswith("Likely rationale")


def test_describe_move_is_type_aware():
    base = {"adds": [{"name": "Player X"}], "drops": [{"name": "Player Y"}]}
    assert describe_move({**base, "type": "waiver", "faab": 31}) == \
        "Claimed Player X for 31 FAAB · dropped Player Y"
    assert describe_move({**base, "type": "waiver", "faab": None}) == \
        "Claimed Player X off waivers · dropped Player Y"
    assert describe_move({**base, "type": "free agent", "faab": None}) == \
        "Added Player X (free agent) · dropped Player Y"
    assert describe_move({"adds": [], "drops": [{"name": "Player Y"}],
                          "type": "free agent", "faab": None}) == \
        "Dropped Player Y"


def test_editorial_priority_orders_stories():
    trade = {"rationale": {"kind": "trade"}, "faab_share": 0}
    faab = {"rationale": {"kind": "weakness", "confidence": "high"},
            "faab_share": 0.31}
    stream = {"rationale": {"kind": "streaming"}, "faab_share": 0}
    assert _priority(trade) > _priority(faab) > _priority(stream)
