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
from leaguepage.matchup_analysis import faab_cost as _bid
from leaguepage.matchup_analysis import weekly_scores

MAX_SCAN_WEEK = 18
STREAM_POSITIONS = {"K", "DEF"}
FAAB_NOTABLE_SHARE = 0.15       # bid worth a sentence
FAAB_MEANINGFUL_SHARE = 0.20    # bid that alone makes a move significant
VALUE_MEANINGFUL = 100.0        # add of a genuinely valuable player
USABLE_VALUE = 60.0             # startable-quality threshold (ref rank ~190)

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


def _bottom(rank: int | None, n: int, share: float) -> bool:
    return rank is not None and rank > n - max(1, round(share * n))


def _top(rank: int | None, n: int, share: float) -> bool:
    return rank is not None and rank <= max(1, round(share * n))


def _usable_counts(rosters: list[dict], values: dict) -> dict:
    """(rid, pos) -> count of players at startable-quality value. Raw roster
    count is NOT depth: two studs plus six fringe receivers is not the
    league's deepest WR room."""
    out: dict = {}
    for r in rosters:
        rid = r["roster_id"]
        for pid in (r.get("players") or []):
            pv = values.get(pid)
            if pv and pv["value"] >= USABLE_VALUE:
                key = (rid, pv["position"])
                out[key] = out.get(key, 0) + 1
    return out


def _rationale(storage: Storage, league: League, tx: dict, ctx: dict | None,
               profile: dict, values: dict, adds: list[dict],
               drops: list[dict], rid: int, faab_share: float,
               usable: dict | None = None) -> dict:
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
            # surplus means USABLE surplus: strong depth behind the starters
            # and multiple startable-quality players, not a big raw count
            depth_rank = profile["depth_ranks"].get(drop["position"], {}).get(rid)
            demand = (profile["teams"].get(rid, {})
                      .get(drop["position"], {}).get("starters_used", 0))
            spare = (usable or {}).get((rid, drop["position"]), 0) - demand
            if _top(depth_rank, n, 0.3) and spare >= 2:
                if kind is None:
                    kind, confidence = "surplus", "medium"
                sentences.append(
                    f"The drop comes from real surplus: {drop['position']} "
                    f"depth ranks #{depth_rank} of {n} with {spare} "
                    "startable-quality players beyond the lineup demand.")

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

    # questionable requires genuinely conflicting roster logic: the drop
    # side must be materially bad (giving up value from an already-weak
    # room) AND at least one more wrong-direction signal. Lower consensus
    # value or an already-good position alone never qualifies — the model
    # cannot see a manager's speculative thesis.
    if kind is None and add and drop:
        a_rank = profile["ranks"].get(add["position"], {}).get(rid)
        d_rank = profile["ranks"].get(drop["position"], {}).get(rid)
        av = (values.get(add["pid"]) or {}).get("value", 0)
        dv = (values.get(drop["pid"]) or {}).get("value", 0)
        drop_bad = _bottom(d_rank, n, 0.3) and dv > 0
        add_wrong = _top(a_rank, n, 0.3)
        value_down = dv > av > 0 or (dv > 0 and av == 0)
        if drop_bad and (add_wrong or value_down):
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
    if confidence == "low":
        # a weak story is not a story: keep the strongest fact, no narrative
        return {"kind": "unclear", "confidence": "low",
                "text": "Rationale unclear. " + (sentences[0] if sentences
                                                 else "")}
    prefix = ("Likely rationale" if confidence == "high"
              else "One plausible rationale")
    return {"kind": kind, "confidence": confidence,
            "text": f"{prefix}: " + " ".join(sentences)}


def _player_since(storage: Storage, league: League, pid: str, rid: int | None,
                  after_week: int, through_week: int) -> dict:
    """Starts, points and weeks for one player since a move.

    `rid` of None follows the player wherever he went, which is how the
    dropped side gets read: the interesting question about a drop is what he
    did next, and for whom.
    """
    starts = pts = weeks = 0
    holder = None
    for wk in range(after_week + 1, through_week + 1):
        for row in storage.get_matchups(league.league_id, wk):
            if rid is not None and row.get("roster_id") != rid:
                continue
            pp = (row.get("players_points") or {}).get(pid)
            started = pid in (row.get("starters") or [])
            if pp is None and not started:
                continue
            holder = row.get("roster_id")
            weeks += 1
            pts += float(pp or 0)
            starts += 1 if started else 0
    return {"starts": starts, "points": round(pts, 1), "weeks": weeks,
            "roster_id": holder,
            "ppg": round(pts / starts, 1) if starts else None}


def how_it_aged(storage: Storage, league: League, row: dict, *,
                through_week: int, names: dict[int, str] | None = None,
                profile: dict | None = None,
                context: dict | None = None) -> dict | None:
    """What actually happened after a move, as a separate column.

    The rationale recorded at the time is never rewritten -- rewriting it
    with hindsight would be inventing a reason he never had. This answers a
    different question: did the thing he was reaching for arrive?

    Three parts, each of which can be absent: what the added players did,
    what the dropped players did next and for whom, and whether the room the
    move was aimed at actually moved.
    """
    names = names or {}
    wk = row.get("week")
    if wk is None or through_week <= wk:
        return None
    added, dropped = [], []
    for a in row.get("adds") or []:
        since = _player_since(storage, league, a["pid"], a["rid"], wk, through_week)
        if since["weeks"]:
            added.append({**since, "name": a.get("name") or a["pid"],
                          "position": a.get("pos")})
    for d in row.get("drops") or []:
        since = _player_since(storage, league, d["pid"], None, wk, through_week)
        if since["weeks"]:
            dropped.append({**since, "name": d.get("name") or d["pid"],
                            "position": d.get("pos"),
                            "claimed_by": names.get(since["roster_id"])})
    room = None
    ctx = context or {}
    if profile and ctx.get("adds"):
        for pid, meta in ctx["adds"].items():
            pos, before = meta.get("pos"), meta.get("after")
            ranks = (profile.get("ranks") or {}).get(pos) or {}
            now = ranks.get(row["rids"][0]) if row.get("rids") else None
            if before and now and pos:
                room = {"position": pos, "before": before, "now": now,
                        "solved": now < before}
            break
    if not (added or dropped or room):
        return None
    return {"added": added, "dropped": dropped, "room": room,
            "through_week": through_week}


def aged_line(aged: dict) -> str:
    """One sentence a reader can take at face value."""
    bits = []
    for a in aged["added"]:
        if a["starts"]:
            bits.append(f"{a['name']} has started {a['starts']} of "
                        f"{a['weeks']} weeks since, for {a['points']:g} points")
        else:
            bits.append(f"{a['name']} has not started since")
    for d in aged["dropped"]:
        where = f" for {d['claimed_by']}" if d.get("claimed_by") else " elsewhere"
        if d["starts"]:
            bits.append(f"{d['name']} has scored {d['points']:g}{where}")
        else:
            bits.append(f"{d['name']} has not started since{where}")
    room = aged.get("room")
    if room:
        if room["solved"]:
            bits.append(f"the {room['position']} room went "
                        f"#{room['before']} to #{room['now']}")
        else:
            bits.append(f"the {room['position']} room is still #{room['now']}")
    if not bits:
        return ""
    # str.capitalize lowercases the rest, which turned "New Back" into "New
    # back" and "the RB room" into "the rb room".
    line = "; ".join(bits)
    return line[0].upper() + line[1:] + "."


def _outcome(storage: Storage, league: League, pid: str, rid: int,
             after_week: int, through_week: int) -> str | None:
    """What happened since the add, kept separate from the at-the-time
    rationale, which is never rewritten."""
    since = _player_since(storage, league, pid, rid, after_week, through_week)
    if not since["weeks"] and not since["starts"]:
        return None
    if since["starts"]:
        return (f"Since the move: started {since['starts']}x for "
                f"{since['points']:g} points.")
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
    usable = _usable_counts(storage.get_rosters(league.league_id), values)
    from leaguepage.team_names import resolve_public_names

    public_names = {rid: (v["name"] or f"Roster {rid}")
                    for rid, v in resolve_public_names(storage, league).items()}

    # player_values covers ROSTERED players only; a dropped player is off
    # every roster, so without this its value would silently read 0 and
    # drop-side comparisons (questionable detection) could never fire.
    from leaguepage.adp import load_adp_for_league

    ref = adp if adp is not None else load_adp_for_league(league)
    for wk in range(0, MAX_SCAN_WEEK + 1):
        for tx in storage.get_transactions(league.league_id, wk):
            for pid in list(tx.get("adds") or {}) + list(tx.get("drops") or {}):
                if pid in values or ref is None:
                    continue
                p = storage.get_player(pid) or {}
                name = p.get("full_name") or " ".join(
                    filter(None, [p.get("first_name"), p.get("last_name")]))
                rank = ref.lookup(name, p.get("position")) if name else None
                if rank is not None:
                    values[pid] = {"name": name,
                                   "position": (p.get("position") or "").upper(),
                                   "value": max(0.0, 250.0 - float(rank))}

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
                    rid, faab_share, usable) if rid is not None else \
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
                aged = how_it_aged(storage, league, row,
                                   through_week=latest_played,
                                   names=public_names, profile=profile,
                                   context=ctx)
                if aged:
                    row["aged"] = aged
                    row["aged_line"] = aged_line(aged)
            row["priority"] = _priority(row)
            out.append(row)
    out.sort(key=lambda r: (-(r["week"]), -(r["created"] or 0)))
    return out


def _priority(row: dict) -> int:
    """Editorial ranking for the curated Force Flow section: trades, big
    FAAB, weakness fixes, and questionable moves outrank routine churn."""
    r = row.get("rationale") or {}
    score = 0
    if r.get("kind") == "trade":
        score += 150
    if row.get("faab_share", 0) >= FAAB_MEANINGFUL_SHARE:
        score += 60 + round(row["faab_share"] * 40)
    if r.get("kind") == "questionable":
        score += 55
    if r.get("kind") == "weakness" and r.get("confidence") == "high":
        score += 50
    if row.get("rank_shift"):
        score += 25
    if (row.get("outcome") or "").startswith("Since the move: started"):
        score += 15
    if r.get("kind") == "streaming":
        score -= 40
    return score


def describe_move(row: dict) -> str:
    """Type-aware one-line description: the transaction kind and cost are
    part of the story ('Claimed X for 31 FAAB' beats 'Added X')."""
    adds = ", ".join(a["name"] for a in row["adds"])
    drops = ", ".join(d["name"] for d in row["drops"])
    t = row.get("type") or ""
    if t == "trade":
        return f"Trade: {adds or '—'} ⇄ {drops or '—'}"
    if adds and t == "waiver":
        verb = (f"Claimed {adds} for {row['faab']} FAAB"
                if row.get("faab") else f"Claimed {adds} off waivers")
    elif adds:
        verb = f"Added {adds} (free agent)"
    else:
        return f"Dropped {drops}"
    return f"{verb} · dropped {drops}" if drops else verb


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
