"""Deterministic transaction rationale: what roster problem might a move
have been addressing?

Hard rule: League-Page never claims to know a manager's actual motive.
Every rationale is inferred from roster context and worded that way
("Likely rationale:", "The roster context suggests..."). When no
defensible roster-based explanation exists, the move is labeled
"Rationale unclear" instead of being given an invented story; when
multiple objective signals point the wrong way, it may be labeled a
questionable move — critique of the move, never the manager.

Signals used (all deterministic, all from synced data): positional room
rank before/after (persisted at sync time, see record_transaction_contexts),
starter/depth structure, player values, injury status, FAAB share, and
K/DST streaming patterns. Bye-week reasoning is deliberately absent: the
synced player data carries no bye information, and rules fire only on data
that exists.

Confidence (high/medium/low) is internal; it selects wording ("Likely"
vs "Possible") and never ships as a field in public output.
"""
from __future__ import annotations

import datetime as dt
import json

from leaguepage.config import League
from leaguepage.storage import Storage
from leaguepage.team_analytics import player_values, positional_profile
from leaguepage.matchup_analysis import weekly_scores

MAX_SCAN_WEEK = 18
STREAM_POSITIONS = {"K", "DEF"}
FAAB_NOTABLE_SHARE = 0.15       # bid worth a sentence
FAAB_MEANINGFUL_SHARE = 0.20    # bid that alone makes a move significant
VALUE_MEANINGFUL = 100.0        # add of a genuinely valuable player

OUT_STATUSES = {"IR", "Out", "Doubtful", "PUP", "Sus", "NA"}


# ------------------------------------------------- sync-time context

def _ctx_key(league: League, txn_id: str) -> str:
    return f"txn_ctx:{league.slug}:{txn_id}"


def record_transaction_contexts(storage: Storage, league: League,
                                *, adp=None) -> int:
    """Persist before/after positional ranks for transactions that do not
    have a context yet. Run from sync, close to when the move happened, so
    the ranks reflect values current AT THAT TIME; render never recomputes
    them later with drifted values (spec: no reconstructed deltas).

    The 'before' roster is reconstructed by reversing the move against the
    current roster; if the players involved have since moved again the
    reconstruction is untrustworthy and no context is stored — renderers
    then simply omit the before/after line."""
    rosters = storage.get_rosters(league.league_id)
    by_rid = {r["roster_id"]: r for r in rosters}
    scores = weekly_scores(storage, league.league_id, MAX_SCAN_WEEK)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    stored = 0
    for wk in range(0, MAX_SCAN_WEEK + 1):
        for tx in storage.get_transactions(league.league_id, wk):
            if tx.get("status") != "complete" or tx.get("type") == "trade":
                continue
            txn_id = str(tx.get("transaction_id") or f"{wk}:{tx.get('created')}")
            if storage.get_meta(_ctx_key(league, txn_id)):
                continue
            adds = tx.get("adds") or {}
            drops = tx.get("drops") or {}
            if not adds and not drops:
                continue
            # trustworthy only while the current roster still reflects it
            clean = all(pid in (by_rid.get(rid, {}).get("players") or [])
                        for pid, rid in adds.items())
            clean = clean and all(
                pid not in (by_rid.get(rid, {}).get("players") or [])
                for pid, rid in drops.items())
            if not clean:
                continue
            before = [dict(r, players=list(r.get("players") or []))
                      for r in rosters]
            b_rid = {r["roster_id"]: r for r in before}
            for pid, rid in adds.items():
                if pid in b_rid[rid]["players"]:
                    b_rid[rid]["players"].remove(pid)
            for pid, rid in drops.items():
                b_rid[rid]["players"].append(pid)
            p_before = positional_profile(storage, league, adp=adp,
                                          weeks_played=weeks_played,
                                          rosters=before)
            p_after = positional_profile(storage, league, adp=adp,
                                         weeks_played=weeks_played,
                                         rosters=rosters)

            def _rank_pair(pid: str, rid: int) -> dict | None:
                p = storage.get_player(pid) or {}
                pos = (p.get("position") or "").upper()
                if pos not in p_after["positions"]:
                    return None
                return {"pos": pos,
                        "before": p_before["ranks"][pos].get(rid),
                        "after": p_after["ranks"][pos].get(rid)}

            ctx = {"computed_at": dt.date.today().isoformat(), "week": wk,
                   "n": p_after["n"], "stage": p_after["stage"],
                   "adds": {pid: _rank_pair(pid, rid)
                            for pid, rid in adds.items()},
                   "drops": {pid: _rank_pair(pid, rid)
                             for pid, rid in drops.items()}}
            storage.set_meta(_ctx_key(league, txn_id), json.dumps(ctx))
            stored += 1
    return stored


# ------------------------------------------------------ the analyzer

def _player(storage: Storage, pid: str) -> dict:
    p = storage.get_player(pid) or {}
    name = p.get("full_name") or " ".join(
        filter(None, [p.get("first_name"), p.get("last_name")])).strip()
    return {"pid": pid, "name": name or pid,
            "position": (p.get("position") or "").upper() or "?",
            "injury_status": p.get("injury_status")}


def _bid(tx: dict) -> int:
    """FAAB cost: waiver claims carry it in settings.waiver_bid; trades
    move budget through waiver_budget entries."""
    bid = (tx.get("settings") or {}).get("waiver_bid") or 0
    bid += sum(x.get("amount", 0) for x in (tx.get("waiver_budget") or []))
    return int(bid)


def _bottom(rank: int | None, n: int, share: float) -> bool:
    return rank is not None and rank > n - max(1, round(share * n))


def _top(rank: int | None, n: int, share: float) -> bool:
    return rank is not None and rank <= max(1, round(share * n))


def _rationale(storage: Storage, league: League, tx: dict, ctx: dict | None,
               profile: dict, values: dict, adds: list[dict],
               drops: list[dict], rid: int, faab_share: float) -> dict:
    """One transaction's inferred rationale: {'kind','confidence','text'}.
    Text is complete and public-safe; confidence stays internal."""
    n = profile["n"]

    # streaming: pure K/DST churn gets a light touch, not analysis theater
    if adds and all(a["position"] in STREAM_POSITIONS for a in adds) and \
            all(d["position"] in STREAM_POSITIONS for d in (drops or adds)):
        return {"kind": "streaming", "confidence": "high",
                "text": "Likely streaming behavior: routine "
                        f"{adds[0]['position']} churn."}

    def _ctx_rank(side: str, pid: str) -> dict:
        return ((ctx or {}).get(side) or {}).get(pid) or {}

    sentences: list[str] = []
    kind = None
    confidence = "low"

    add = adds[0] if adds else None
    drop = drops[0] if drops else None
    add_rank_before = None
    add_rank_after = None
    if add:
        pair = _ctx_rank("adds", add["pid"])
        add_rank_before, add_rank_after = pair.get("before"), pair.get("after")
        current_rank = profile["ranks"].get(add["position"], {}).get(rid)
        rank_for_need = add_rank_before if add_rank_before is not None else current_rank

        if _bottom(rank_for_need, n, 0.34):
            kind, confidence = "weakness", "high" if _bottom(rank_for_need, n, 0.25) else "medium"
            s = (f"This appears aimed at {add['position']}: the room ranked "
                 f"#{rank_for_need} of {n} before the move")
            if add_rank_after is not None and add_rank_after < rank_for_need:
                s += f", and the addition moved it to #{add_rank_after}"
            sentences.append(s + ".")
        elif rank_for_need is not None:
            t = profile["teams"].get(rid, {}).get(add["position"])
            starter_rank = profile["starter_ranks"].get(add["position"], {}).get(rid)
            depth_rank = profile["depth_ranks"].get(add["position"], {}).get(rid)
            if (t and _top(starter_rank, n, 0.4) and _bottom(depth_rank, n, 0.25)):
                kind, confidence = "depth", "medium"
                sentences.append(
                    f"The roster context suggests a depth play: "
                    f"{add['position']} starters rank #{starter_rank} of {n} "
                    f"but the depth behind them ranked #{depth_rank}.")

    if drop:
        if (drop.get("injury_status") or "") in OUT_STATUSES:
            if kind is None:
                kind, confidence = "injury", "high"
            sentences.append(
                f"{drop['name']} is currently listed "
                f"{drop['injury_status']}, which makes the drop side "
                "straightforward.")
        else:
            drop_rank = profile["ranks"].get(drop["position"], {}).get(rid)
            room = profile["teams"].get(rid, {}).get(drop["position"]) or {}
            if _top(drop_rank, n, 0.3) and room.get("count", 0) >= 4:
                if kind is None:
                    kind, confidence = "surplus", "medium"
                sentences.append(
                    f"The drop comes from the roster's deepest territory: "
                    f"{drop['position']} ranked #{drop_rank} of {n} with "
                    f"{room['count']} players rostered.")

    # rebalance: strong drop-room feeding a weak add-room
    if add and drop and kind in (None, "surplus"):
        a_rank = add_rank_before if add_rank_before is not None else \
            profile["ranks"].get(add["position"], {}).get(rid)
        d_rank = profile["ranks"].get(drop["position"], {}).get(rid)
        if (a_rank is not None and d_rank is not None
                and a_rank - d_rank >= max(2, round(n / 3))):
            kind, confidence = "rebalance", "medium"
            sentences.insert(0, (
                f"One plausible reason is rebalance: the move shifts a spot "
                f"from the #{d_rank} {drop['position']} room to the "
                f"#{a_rank} {add['position']} room."))

    # questionable: multiple wrong-direction signals AND no positive story
    if kind is None and add and drop:
        wrong = 0
        a_rank = profile["ranks"].get(add["position"], {}).get(rid)
        d_rank = profile["ranks"].get(drop["position"], {}).get(rid)
        if _top(a_rank, n, 0.3):
            wrong += 1
        if _bottom(d_rank, n, 0.3):
            wrong += 1
        av = (values.get(add["pid"]) or {}).get("value", 0)
        dv = (values.get(drop["pid"]) or {}).get("value", 0)
        if dv > av > 0 or (dv > 0 and av == 0):
            wrong += 1
        if wrong >= 2:
            return {"kind": "questionable", "confidence": "medium",
                    "text": (
                        f"Questionable fit on the current data: the team "
                        f"already ranks #{a_rank} of {n} at "
                        f"{add['position']} and gave up "
                        f"{drop['name']} from its #{d_rank} "
                        f"{drop['position']} room. No injury or obvious "
                        "starter upgrade in the synced data explains the "
                        "tradeoff.")}

    if kind is None:
        return {"kind": "unclear", "confidence": "low",
                "text": "Rationale unclear from roster context alone; "
                        "nothing in the current data marks an obvious need "
                        "this addresses."}

    if faab_share >= FAAB_NOTABLE_SHARE:
        sentences.append(
            f"The {round(faab_share * 100)}% FAAB bid suggests this was "
            "more than a speculative bench add.")
    prefix = "Likely rationale" if confidence == "high" else "Possible rationale"
    return {"kind": kind, "confidence": confidence,
            "text": f"{prefix}: " + " ".join(sentences)}


def _outcome(storage: Storage, league: League, pid: str, rid: int,
             after_week: int, through_week: int) -> str | None:
    """What happened since the add — kept separate from the at-the-time
    rationale, which is never rewritten."""
    starts, pts, seen = 0, 0.0, False
    for wk in range(after_week + 1, through_week + 1):
        for row in storage.get_matchups(league.league_id, wk):
            if row.get("roster_id") != rid:
                continue
            pp = (row.get("players_points") or {}).get(pid)
            if pp is not None:
                pts += float(pp)
                seen = True
            if pid in (row.get("starters") or []):
                starts += 1
    if not seen and starts == 0:
        return None
    if starts:
        return (f"Since the move: started {starts}x for {pts:g} points.")
    return "The player has yet to enter the starting lineup."


def analyze_transactions(storage: Storage, league: League,
                         through_week: int, *, adp=None) -> list[dict]:
    """All completed transactions with facts + inferred rationale, newest
    first. Public-safe except the 'confidence' field, which callers must
    not render."""
    league_data = storage.get_league(league.league_id) or {}
    budget = float((league_data.get("settings") or {})
                   .get("waiver_budget") or 100)
    values, _stage = player_values(storage, league, adp=adp)
    scores = weekly_scores(storage, league.league_id, MAX_SCAN_WEEK)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    latest_played = max((wk for rows in scores.values() for wk, _ in rows),
                        default=0)
    profile = positional_profile(storage, league, adp=adp,
                                 weeks_played=weeks_played)

    out: list[dict] = []
    for wk in range(0, MAX_SCAN_WEEK + 1):
        for tx in storage.get_transactions(league.league_id, wk):
            if tx.get("status") != "complete":
                continue
            adds = [dict(_player(storage, pid), rid=rid)
                    for pid, rid in (tx.get("adds") or {}).items()]
            drops = [dict(_player(storage, pid), rid=rid)
                     for pid, rid in (tx.get("drops") or {}).items()]
            if not adds and not drops:
                continue
            bid = _bid(tx)
            faab_share = bid / budget if budget else 0.0
            rids = sorted({p["rid"] for p in adds + drops})
            txn_id = str(tx.get("transaction_id")
                         or f"{wk}:{tx.get('created')}")
            row = {
                "txn_id": txn_id, "week": wk,
                "type": (tx.get("type") or "?").replace("_", " "),
                "created": tx.get("created"),
                "rids": rids, "adds": adds, "drops": drops,
                "faab": bid or None, "faab_share": round(faab_share, 2),
            }
            if tx.get("type") == "trade":
                row["rationale"] = {"kind": "trade", "confidence": "high",
                                    "text": None}
                row["significant"] = True
            else:
                ctx_raw = storage.get_meta(_ctx_key(league, txn_id))
                ctx = json.loads(ctx_raw) if ctx_raw else None
                rid = rids[0] if rids else None
                row["rationale"] = _rationale(
                    storage, league, tx, ctx, profile, values, adds, drops,
                    rid, faab_share) if rid is not None else \
                    {"kind": "unclear", "confidence": "low",
                     "text": "Rationale unclear."}
                pair = ((ctx or {}).get("adds") or {}).get(
                    adds[0]["pid"]) if adds else None
                if pair and pair.get("before") is not None \
                        and pair.get("after") is not None \
                        and pair["before"] != pair["after"]:
                    row["rank_shift"] = (f"{pair['pos']} "
                                         f"#{pair['before']} → "
                                         f"#{pair['after']}")
                improved = bool(pair and pair.get("before") is not None
                                and pair.get("after") is not None
                                and pair["before"] - pair["after"] >= 3)
                add_value = max((values.get(a["pid"], {}).get("value", 0)
                                 for a in adds), default=0)
                row["significant"] = (
                    faab_share >= FAAB_MEANINGFUL_SHARE
                    or add_value >= VALUE_MEANINGFUL
                    or row["rationale"]["kind"] == "questionable"
                    or (row["rationale"]["kind"] == "weakness"
                        and row["rationale"]["confidence"] == "high")
                    or improved)
                if row["rationale"]["kind"] == "streaming" \
                        and faab_share < FAAB_MEANINGFUL_SHARE:
                    row["significant"] = False
                if adds and latest_played > wk:
                    row["outcome"] = _outcome(storage, league,
                                              adds[0]["pid"], adds[0]["rid"],
                                              wk, latest_played)
            out.append(row)
    out.sort(key=lambda r: (-(r["week"]), -(r["created"] or 0)))
    return out


def transaction_story_candidates(storage: Storage, league: League,
                                 through_week: int) -> list[dict]:
    """Story Board candidates from meaningful move analysis."""
    cands = []
    for row in analyze_transactions(storage, league, through_week):
        if not row.get("significant"):
            continue
        r = row["rationale"]
        names = ", ".join(a["name"] for a in row["adds"]) or \
            ", ".join(d["name"] for d in row["drops"])
        if r["kind"] == "questionable":
            angle = "a move the roster math argues with"
        elif row.get("rank_shift"):
            angle = f"positional shift {row['rank_shift']}"
        elif row["faab_share"] >= FAAB_MEANINGFUL_SHARE:
            angle = f"{round(row['faab_share'] * 100)}% of FAAB committed"
        else:
            angle = "a consequential pickup"
        cands.append({
            "kind": "transaction", "week": row["week"],
            "headline": f"Week {row['week']}: {names} ({row['type']})",
            "angle": angle,
            "support": [s for s in [r.get("text"), row.get("rank_shift"),
                                    row.get("outcome")] if s],
        })
    return cands[:6]
