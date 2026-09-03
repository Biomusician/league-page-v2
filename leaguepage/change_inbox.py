"""Commissioner Change Inbox — "what changed since my last sync?"

The Desk already computes plenty. What it never did was tell the commissioner
where to look after pressing Sync, which meant opening five pages and diffing
them by eye. This module answers that in one screen.

Two halves:

* **Change detection.** Every sync stores a compact snapshot of league state
  (`sync_snapshots`). Comparing the current snapshot against the baseline
  yields typed items that carry BEFORE and AFTER, not just a headline, because
  "moved from 8th to 3rd" is the story and "3rd" is only a fact.
* **Merge and rank.** Change items join the existing weekly story candidates
  (`weekly_signals.weekly_story_candidates`, which already aggregates matchup
  interest, results, force flow, tracks/fades, black box, takes, coalitions
  and analytics deltas) and the whole set is scored by `significance`.

Deliberately reused rather than rebuilt: decisions live in `story_decisions`,
which has carried include/ignore/save plus a destination route since the Story
Board shipped. Add to Issue, Ignore This Week and Save for Later are those
three values, so an inbox decision is the same decision the issue builder and
the authoring briefs already read. No second decision store exists.

Everything here is private. Nothing in this module reaches `dist/`.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.matchup_analysis import faab_cost
from leaguepage.storage import Storage

# A change has to clear its category's floor to become an item at all. Scoring
# a trivial change low is not enough: the inbox has to be short, so noise is
# dropped before it can accumulate small positives.
FLOORS = {
    "standings_rank": 2,       # places moved
    "playoff_odds": 0.08,      # absolute probability change
    "positional_rank": 3,      # places moved in a positional room
    "faab_share": 0.15,        # share of the waiver budget
    "margin_blowout": 35.0,    # points
    "margin_narrow": 5.0,      # points
}

# Denominators that turn a raw change into a 0..1 magnitude inside its own
# category, so categories are comparable without any of them being hardcoded
# as automatically important.
SCALES = {
    "playoff_odds": 0.50,      # a 50-point swing is a full-scale event
    "margin_blowout": 70.0,
    "score_extreme": 40.0,     # points beyond the previous season extreme
}

# Destinations offered on an inbox item, matching the Story Board's existing
# vocabulary exactly so both surfaces write the same story_decisions.route.
ROUTE_CHOICES = [
    ("lowdown", "Lowdown"), ("matchup", "Matchup"), ("awards", "Award consideration"),
    ("tracks", "Tracks of Interest"), ("fades", "Fades"),
    ("force-flow", "Force Flow"), ("black-box", "Black Box"),
    ("false-assumptions", "False Assumptions"), ("custom", "Custom section"),
]
SURFEIT_ONLY_ROUTES = {"false-assumptions"}

CATEGORY_SECTIONS = {
    "result": ["lowdown", "ctp"],
    "standings": ["lowdown", "tracks"],
    "playoff": ["lowdown", "tracks", "intel"],
    "strength": ["tracks", "forceflow"],
    "transaction": ["forceflow", "tracks"],
    "record": ["blackbox", "lowdown"],
    "receipt": ["false-assumptions", "lowdown"],
    "matchup": ["ctp", "lowdown"],
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def ordinal(n) -> str:
    """1st/2nd/3rd/11th. Standings copy is read by a person, so "2th" is a bug."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# ------------------------------------------------------------- state capture

def capture_state(storage: Storage, league: League, season: str, week: int,
                  *, profile: dict | None = None, outlook: dict | None = None) -> dict:
    """A compact, JSON-safe picture of everything the inbox can diff.

    `profile` and `outlook` are accepted so a caller that already computed
    them (the sync job does) pays for them once."""
    from leaguepage.matchup_analysis import all_play, team_record, weekly_scores
    from leaguepage.team_analytics import playoff_outlook, positional_profile, scoring_streaks

    scores = weekly_scores(storage, league.league_id, max(week, 1))
    weeks_played = max((len(v) for v in scores.values()), default=0)
    profile = profile or positional_profile(storage, league, weeks_played=weeks_played)
    outlook = outlook if outlook is not None else playoff_outlook(storage, league, max(week, 1))

    rosters = storage.get_rosters(league.league_id)
    recs = {r["roster_id"]: team_record(r) for r in rosters}
    order = sorted(recs, key=lambda rid: (-recs[rid]["wins"], -recs[rid]["fpts"]))
    ap = all_play(scores)

    results: dict[str, list] = {}
    for wk in range(1, max(week, 1) + 1):
        rows = [r for r in storage.get_matchups(league.league_id, wk)
                if r.get("matchup_id") is not None]
        by_mid: dict[int, list] = {}
        for r in rows:
            by_mid.setdefault(r["matchup_id"], []).append(r)
        for mid, pair in by_mid.items():
            if len(pair) != 2:
                continue
            a, b = pair
            pa, pb = float(a.get("points") or 0), float(b.get("points") or 0)
            if pa <= 0 and pb <= 0:
                continue  # not played yet
            results[f"{wk}:{mid}"] = [a["roster_id"], round(pa, 2),
                                      b["roster_id"], round(pb, 2)]

    tx_ids = []
    for wk in range(1, max(week, 1) + 1):
        for t in storage.get_transactions(league.league_id, wk):
            if t.get("status") == "complete" and t.get("transaction_id"):
                tx_ids.append(str(t["transaction_id"]))

    all_pts = [p for rows in scores.values() for _, p in rows]
    return {
        "week": int(week),
        "weeks_played": weeks_played,
        "n_teams": len(rosters),
        "standings": {str(rid): i + 1 for i, rid in enumerate(order)},
        "records": {str(rid): [recs[rid]["wins"], recs[rid]["losses"]] for rid in recs},
        "points_for": {str(rid): round(recs[rid]["fpts"], 2) for rid in recs},
        "all_play": {str(rid): [v["wins"], v["losses"]] for rid, v in ap.items()},
        "playoff": ({str(rid): round(t["odds"], 4) for rid, t in outlook["teams"].items()}
                    if "teams" in outlook else None),
        "playoff_spots": outlook.get("playoff_teams"),
        "positional_ranks": {pos: {str(rid): r for rid, r in profile["ranks"][pos].items()}
                             for pos in profile["positions"]},
        "streaks": {str(rid): [v["kind"], v["length"]]
                    for rid, v in scoring_streaks(storage, league, max(week, 1)).items()},
        "results": results,
        "transactions": sorted(tx_ids),
        "high_score": round(max(all_pts), 2) if all_pts else None,
        "low_score": round(min(all_pts), 2) if all_pts else None,
    }


def record(storage: Storage, league: League, season: str, week: int,
           **kwargs) -> dict | None:
    """Capture and store. Returns the stored snapshot, or None when this sync
    changed nothing."""
    payload = capture_state(storage, league, season, week, **kwargs)
    return storage.record_sync_snapshot(league_slug=league.slug, season=season,
                                        week=week, payload=payload)


# Why a reader would care, as opposed to why the ranker ranked it. `explain`
# answers the second question well and the first not at all: "+12 standings
# or playoff consequence (50%)" is a scoring line, and 50% is not a reason.
MATTERS = {
    "result": "Results are the only thing the standings are made of.",
    "standings": "The table is what everyone checks first.",
    "playoff": "This moves who is actually going to be playing in December.",
    "strength": "Roster construction is the thing that keeps producing "
                "results after this week.",
    "transaction": "Somebody spent something to change their team.",
    "record": "A season mark is the kind of thing the Black Box exists for.",
    "receipt": "A published claim has moved, one way or the other.",
    "matchup": "This is a game somebody will want previewed.",
}


def _matters(item: dict) -> str:
    """One sentence on why this is worth a reader's attention."""
    if item.get("consequence_label"):
        return f"{MATTERS.get(item.get('category'), '')} {item['consequence_label'].capitalize()}.".strip()
    return MATTERS.get(item.get("category"), "")


# ------------------------------------------------------------ change items

def _item(item_id, category, headline, *, what_changed, before=None, after=None,
          magnitude=0.0, teams=None, facts=None, evidence=None, **signals) -> dict:
    return {
        "item_id": item_id, "category": category, "headline": headline,
        "what_changed": what_changed, "before": before, "after": after,
        "magnitude": _clamp01(magnitude),
        "teams": teams or [], "facts": facts or [], "evidence": evidence or [],
        "sections": CATEGORY_SECTIONS.get(category, ["lowdown"]),
        "source": "change",
        **signals,
    }


def _names(storage: Storage, league: League) -> dict[int, str]:
    from leaguepage.team_names import resolve_public_names

    return {rid: (v["name"] or f"Roster {rid}")
            for rid, v in resolve_public_names(storage, league).items()}


def _get(d, rid):
    if not d:
        return None
    return d.get(str(rid), d.get(rid))


def diff_snapshots(before: dict, after: dict, names: dict[int, str],
                   *, league_id: str = "") -> list[dict]:
    """Typed change items with before/after. Only material changes survive:
    each category has a floor in FLOORS, applied here rather than downstream,
    because a short inbox is the product."""
    items: list[dict] = []
    n = max(2, after.get("n_teams") or 12)
    spread = n - 1

    def nm(rid):
        return names.get(int(rid), f"Roster {rid}")

    # ---- results that completed since the baseline
    new_results = {k: v for k, v in (after.get("results") or {}).items()
                   if k not in (before.get("results") or {})}
    b_stand = before.get("standings") or {}
    for key, (rid_a, pts_a, rid_b, pts_b) in sorted(new_results.items()):
        wk = key.split(":")[0]
        win_rid, win_pts, lose_rid, lose_pts = (
            (rid_a, pts_a, rid_b, pts_b) if pts_a >= pts_b else (rid_b, pts_b, rid_a, pts_a))
        margin = round(win_pts - lose_pts, 2)
        ev = [f"sleeper:matchup:{league_id}:{wk}:{key.split(':')[1]}"] if league_id else []
        seed_w, seed_l = _get(b_stand, win_rid), _get(b_stand, lose_rid)
        # upset: the lower-seeded team won, scaled by the seed gap it closed
        if seed_w and seed_l and seed_w - seed_l >= 2:
            gap = seed_w - seed_l
            items.append(_item(
                f"change:upset:{key}", "result",
                f"Upset: {nm(win_rid)} beat {nm(lose_rid)}",
                what_changed=f"{nm(win_rid)} was {ordinal(seed_w)} going in and won by {margin:g}",
                before=f"{nm(win_rid)} {ordinal(seed_w)}, {nm(lose_rid)} {ordinal(seed_l)}",
                after=f"{nm(win_rid)} {win_pts:g}, {nm(lose_rid)} {lose_pts:g}",
                magnitude=_clamp01(gap / spread + 0.25), teams=[nm(win_rid), nm(lose_rid)],
                facts=[f"Week {wk}: {win_pts:g} to {lose_pts:g}."], evidence=ev,
                # An upset is rare by construction -- that is what the word
                # means -- and it matters more the higher the team that lost.
                # A fixed 0.5 consequence had a nine-seed beating a three-seed
                # scoring below a bench-piece trade, and filed as Minor.
                consequence=_clamp01(0.35 + 0.65 * (1 - (seed_l - 1) / spread)),
                consequence_label=f"the {ordinal(seed_l)} seed lost",
                rarity=_clamp01(0.3 + gap / spread),
                rarity_label=f"{gap} seeds of upset",
                expectation=_clamp01(gap / spread),
                expectation_label=f"{gap} seeds of upset",
                magnitude_label=f"{gap} seeds"))
        if margin >= FLOORS["margin_blowout"]:
            items.append(_item(
                f"change:blowout:{key}", "result",
                f"Blowout: {nm(win_rid)} by {margin:g}",
                what_changed=f"{margin:g}-point margin",
                before=None, after=f"{win_pts:g} to {lose_pts:g}",
                magnitude=margin / SCALES["margin_blowout"],
                teams=[nm(win_rid), nm(lose_rid)],
                facts=[f"Week {wk} margin of {margin:g}."], evidence=ev,
                rarity=_clamp01((margin - FLOORS["margin_blowout"]) / 40.0),
                magnitude_label=f"{margin:g} points"))
        elif margin <= FLOORS["margin_narrow"]:
            items.append(_item(
                f"change:narrow:{key}", "result",
                f"Photo finish: {nm(win_rid)} by {margin:g}",
                what_changed=f"decided by {margin:g}",
                before=None, after=f"{win_pts:g} to {lose_pts:g}",
                magnitude=(FLOORS["margin_narrow"] - margin) / FLOORS["margin_narrow"],
                teams=[nm(win_rid), nm(lose_rid)],
                facts=[f"Week {wk} margin of {margin:g}."], evidence=ev,
                rarity=0.4, magnitude_label=f"{margin:g}-point margin"))

    # ---- scoring extremes
    for kind, key, better in (("high", "high_score", True), ("low", "low_score", False)):
        b_val, a_val = before.get(key), after.get(key)
        if a_val is None or b_val is None or a_val == b_val:
            continue
        moved = (a_val > b_val) if better else (a_val < b_val)
        if not moved:
            continue
        delta = abs(a_val - b_val)
        holder = None
        for k, (ra, pa, rb, pb) in (after.get("results") or {}).items():
            for rid, pts in ((ra, pa), (rb, pb)):
                if abs(pts - a_val) < 0.01:
                    holder = rid
        items.append(_item(
            f"change:season-{kind}:{a_val}", "record",
            f"New season {kind} score: {nm(holder) if holder else 'a team'} at {a_val:g}",
            what_changed=f"season {kind} moved",
            before=f"{b_val:g}", after=f"{a_val:g}",
            magnitude=_clamp01(delta / SCALES["score_extreme"]),
            teams=[nm(holder)] if holder else [],
            facts=[f"Previous season {kind} was {b_val:g}."],
            rarity=0.8, rarity_label="a season record for this league",
            history=0.5, history_label="it goes in the Black Box",
            magnitude_label=f"{delta:g} points past the old mark"))

    # ---- standings movement.
    # Preseason standings are an arbitrary tiebreak among 0-0 teams, so a diff
    # against a baseline with no games played reports every team as having
    # "moved" and buries the week's actual story. Rank movement only means
    # something once both sides of the comparison have results behind them.
    a_stand = after.get("standings") or {}
    standings_meaningful = (before.get("weeks_played") or 0) > 0
    for rid_key, now in (a_stand.items() if standings_meaningful else []):
        then = _get(b_stand, rid_key)
        if not then or abs(now - then) < FLOORS["standings_rank"]:
            continue
        moved = then - now
        items.append(_item(
            f"change:standings:{rid_key}", "standings",
            f"{nm(rid_key)} moved {abs(moved)} places to {ordinal(now)}",
            what_changed=f"standings {ordinal(then)} to {ordinal(now)}",
            before=ordinal(then), after=ordinal(now),
            magnitude=abs(moved) / spread, teams=[nm(rid_key)],
            facts=[f"Record now {'-'.join(str(x) for x in (_get(after.get('records'), rid_key) or []))}."],
            consequence=_clamp01(abs(moved) / spread + (0.4 if now <= 3 or now >= n - 2 else 0)),
            magnitude_label=f"{abs(moved)} places"))
    # leadership and cellar changes are their own story, not a rank delta
    slots = ((1, "first place", "standings"), (n, "last place", "standings"))
    for slot, label, cat in (slots if standings_meaningful else ()):
        b_who = next((r for r, v in b_stand.items() if v == slot), None)
        a_who = next((r for r, v in a_stand.items() if v == slot), None)
        if b_who and a_who and b_who != a_who:
            items.append(_item(
                f"change:{'leader' if slot == 1 else 'cellar'}", cat,
                f"New {label}: {nm(a_who)}",
                what_changed=f"{label} changed hands",
                before=nm(b_who), after=nm(a_who),
                magnitude=0.8 if slot == 1 else 0.45,
                teams=[nm(a_who), nm(b_who)],
                facts=[f"{nm(b_who)} held {label} at the last sync."],
                consequence=0.9 if slot == 1 else 0.3,
                rarity=0.5, magnitude_label=f"{label} changed hands"))

    # ---- playoff outlook
    b_odds, a_odds = before.get("playoff"), after.get("playoff")
    if b_odds and a_odds:
        for rid_key, now in a_odds.items():
            then = _get(b_odds, rid_key)
            if then is None:
                continue
            delta = now - then
            clinched = now >= 0.99 and then < 0.99
            eliminated = now <= 0.01 and then > 0.01
            if abs(delta) < FLOORS["playoff_odds"] and not (clinched or eliminated):
                continue
            if clinched or eliminated:
                word = "clinched a playoff berth" if clinched else "was eliminated"
                items.append(_item(
                    f"change:playoff-{'clinch' if clinched else 'elim'}:{rid_key}",
                    "playoff", f"{nm(rid_key)} {word}",
                    what_changed=word, before=f"{then:.0%}", after=f"{now:.0%}",
                    magnitude=1.0, teams=[nm(rid_key)],
                    facts=[f"Playoff probability {then:.0%} to {now:.0%}."],
                    consequence=1.0, rarity=0.9,
                    consequence_label="the season's shape is now settled for this team",
                    magnitude_label="decisive"))
                continue
            items.append(_item(
                f"change:playoff:{rid_key}", "playoff",
                f"{nm(rid_key)} playoff odds {then:.0%} to {now:.0%}",
                what_changed=f"playoff probability {delta:+.0%}",
                before=f"{then:.0%}", after=f"{now:.0%}",
                magnitude=_clamp01(abs(delta) / SCALES["playoff_odds"]),
                teams=[nm(rid_key)],
                facts=[f"Moved {abs(delta):.0%} since the last reviewed sync."],
                consequence=_clamp01(abs(delta) / 0.3),
                magnitude_label=f"{abs(delta):.0%} probability"))

    # ---- positional strength
    for pos, ranks in (after.get("positional_ranks") or {}).items():
        b_ranks = (before.get("positional_ranks") or {}).get(pos) or {}
        for rid_key, now in ranks.items():
            then = _get(b_ranks, rid_key)
            if not then or abs(now - then) < FLOORS["positional_rank"]:
                continue
            moved = then - now
            direction = "strengthened" if moved > 0 else "weakened"
            items.append(_item(
                f"change:pos:{pos}:{rid_key}", "strength",
                f"{nm(rid_key)} {pos} room {direction}: #{then} to #{now}",
                what_changed=f"{pos} room #{then} to #{now}",
                before=f"#{then}", after=f"#{now}",
                magnitude=abs(moved) / spread, teams=[nm(rid_key)],
                facts=[f"{pos} room moved {abs(moved)} places among {n} teams."],
                consequence=0.35 if now >= n - 2 or now <= 2 else 0.15,
                magnitude_label=f"{abs(moved)} places at {pos}"))

    return items


# --------------------------------------------------------- transactions

def transaction_items(storage: Storage, league: League, before: dict, after: dict,
                      names: dict[int, str]) -> list[dict]:
    """New completed transactions since the baseline, with cost and intent."""
    seen = set(before.get("transactions") or [])
    fresh = [t for t in (after.get("transactions") or []) if t not in seen]
    if not fresh:
        return []
    budget = float((storage.get_league(league.league_id) or {})
                   .get("settings", {}).get("waiver_budget") or 100)
    wanted = set(fresh)
    out = []
    for wk in range(1, max(after.get("week") or 1, 1) + 1):
        for t in storage.get_transactions(league.league_id, wk):
            tid = str(t.get("transaction_id") or "")
            if tid not in wanted or t.get("status") != "complete":
                continue
            # A claim's price is in settings.waiver_bid; waiver_budget is
            # budget moving between teams in a trade. Reading only the
            # second made every waiver claim free, so a 45%-of-budget claim
            # was filtered out as a routine add/drop and never reached the
            # Commissioner.
            faab = faab_cost(t)
            share = faab / budget if budget else 0.0
            is_trade = t.get("type") == "trade"
            if share < FLOORS["faab_share"] and not is_trade:
                continue  # routine add/drop never reaches the inbox
            adds, teams = [], []
            for pid, rid in (t.get("adds") or {}).items():
                p = storage.get_player(pid) or {}
                adds.append(p.get("full_name") or pid)
                teams.append(names.get(rid, f"Roster {rid}"))
            label = "Trade" if is_trade else f"Waiver claim for {faab} FAAB"
            out.append(_item(
                f"change:txn:{tid}", "transaction",
                f"{label}: {', '.join(sorted(set(adds))[:3]) or 'roster move'}",
                what_changed=label,
                before=None,
                after=f"{', '.join(sorted(set(teams)))} added {', '.join(sorted(set(adds))[:3])}",
                # A trade is not important because it is a trade. It is
                # important when it moves real players or costs real money,
                # so the old floor of 0.55 on every trade -- which put a
                # bench-piece swap above a three-seed upset -- is gone.
                magnitude=_clamp01(max(share, 0.30 + 0.08 * len(set(adds))
                                       if is_trade else 0.0)),
                teams=sorted(set(teams)),
                facts=[f"Week {wk} {t.get('type')}"
                       + (f", {faab} of a {budget:g} budget ({share:.0%})." if faab else ".")],
                evidence=[f"sleeper:transaction:{tid}"],
                # A zero-FAAB trade cost nothing in money. It cost players,
                # which is what magnitude above is measuring, so claiming a
                # cost here as well was scoring the same fact twice.
                cost=_clamp01(share),
                cost_label=(f"{share:.0%} of the waiver budget" if faab
                            else "no money changed hands"),
                magnitude_label=(f"{share:.0%} of budget" if faab else "a trade")))
    return out


# ------------------------------------------------------------- receipts

def receipt_items(storage: Storage, league: League, after: dict,
                  names: dict[int, str]) -> list[dict]:
    """Open takes whose subject just did something. A take is only worth
    resurfacing when new evidence exists, so this reads the current state
    rather than the take table alone."""
    from leaguepage import takes as takes_mod

    out = []
    for t in storage.open_takes(league.slug):
        rid = t.get("subject_roster_id")
        if rid is None:
            rid = {v: k for k, v in names.items()}.get(t.get("subject") or "")
        name = names.get(rid) or t.get("subject_name") or t.get("subject") or "the league"
        rec_status = t.get("recommended_status")
        evidence_lines = t.get("evidence") or []
        # A tracked claim is only inbox-worthy once the engine has actually
        # leaned somewhere. An open take with nothing new to say is not news,
        # and putting it here every week would train him to skip the section.
        if rec_status not in (takes_mod.LEANING_RIGHT, takes_mod.LEANING_WRONG,
                              takes_mod.RESOLVED_RIGHT, takes_mod.RESOLVED_WRONG):
            continue
        if rec_status == t.get("status"):
            continue        # already ruled on; not a new decision for him
        direction = ("evidence supports it" if rec_status in
                     (takes_mod.LEANING_RIGHT, takes_mod.RESOLVED_RIGHT)
                     else "evidence is moving against it")
        out.append(_item(
            f"change:receipt:{t['take_id']}", "receipt",
            f"Receipt ready on {name}",
            what_changed=direction,
            before=f"\"{(t.get('quote') or '')[:110]}\"",
            after=(evidence_lines[0] if evidence_lines else "current state available"),
            magnitude=0.65, teams=[name],
            facts=([f"Tracked in {t.get('issue_key') or t.get('context') or 'an issue'}; "
                    f"engine reads it as {takes_mod.STATUS_LABELS.get(rec_status, rec_status)}."]
                   + list(evidence_lines[:3])),
            evidence=[f"take:{t['take_id']}"],
            receipt=0.95, receipt_label="a tracked claim now has evidence either way",
            history=0.4, magnitude_label="a claim the Commissioner made is testable"))
    return out


# ------------------------------------------------------------------ inbox

def build_inbox(storage: Storage, league: League, season: str,
                *, include_candidates: bool = True, limit: int = 40) -> dict:
    """The Change Inbox for one league: ranked items, decisions applied, and
    enough context for the Desk to render without recomputing anything."""
    from leaguepage import significance

    latest = storage.latest_sync_snapshot(league.slug, season)
    baseline = storage.baseline_sync_snapshot(league.slug, season)
    names = _names(storage, league)
    week = int((latest or {}).get("week") or storage.get_meta("current_week") or 1)
    issue_key = f"week-{week:02d}"

    items: list[dict] = []
    if latest and baseline:
        a, b = latest["payload"], baseline["payload"]
        items += diff_snapshots(b, a, names, league_id=league.league_id)
        items += transaction_items(storage, league, b, a, names)
        items += receipt_items(storage, league, a, names)
        for it in items:
            it["fresh"] = True

    if include_candidates:
        # When there is no baseline the diff produced nothing, so the result
        # candidates are the only coverage of the week and must not be dropped.
        items += _candidate_items(storage, league, season, week,
                                  drop_duplicates=bool(latest and baseline))

    decisions = storage.get_story_decisions(league.slug, season, issue_key)
    prior_lanes = _prior_lanes(storage, league, season, week)
    saved = saved_earlier(storage, league, season, week)

    def ctx_for(item):
        return significance.repetition_context(item, prior_lanes)

    ranked = significance.rank(items, ctx_for)
    for it in ranked:
        d = decisions.get(it["item_id"]) or {}
        # An empty decision is a reopened item, not a decided one.
        it["decision"] = d.get("decision") or None
        it["route"] = d.get("route")
        it["note"] = d.get("note")
        it["why"] = significance.explain(it)
        it["matters"] = it.get("matters") or _matters(it)
        held = saved.get(it["item_id"])
        it["saved_week"] = held
        if held and it["decision"] is None:
            it["what_changed"] = (f"Saved in week {held} and still true. "
                                  + (it.get("what_changed") or "")).strip()

    open_items = [i for i in ranked if i["decision"] is None]
    return {
        "league": league.slug, "season": season, "week": week,
        "issue_key": issue_key,
        "baseline_at": (baseline or {}).get("taken_at"),
        "latest_at": (latest or {}).get("taken_at"),
        "has_baseline": bool(latest and baseline),
        "items": ranked[:limit],
        "open_count": len(open_items),
        "counts": {
            "total": len(ranked),
            "lead": sum(1 for i in ranked if i["significance"]["band"] == "Lead story"),
            "decided": sum(1 for i in ranked if i["decision"]),
        },
    }


# Result candidates the snapshot diff already covers, and covers better,
# because the diff carries before/after and a real magnitude.
DUPLICATED_BY_DIFF = ("story:blowout:", "story:photo-finish:",
                      "story:high-score:", "story:low-score:",
                      # the analytics stream restates what the snapshot diff
                      # already said, with a flat magnitude and no
                      # before/after, so the same fact was triaged twice
                      "analytics:standings:", "analytics:odds:",
                      "analytics:pos:")


# What a candidate is worth before anything specific is known about it.
# Every non-matchup candidate used to arrive at a flat 0.4 with no other
# signal, which meant they all scored exactly 16 and the second half of the
# inbox was sorted alphabetically by id.
CANDIDATE_BASE = {
    "result": 0.55, "record": 0.60, "take": 0.55, "force-flow": 0.45,
    "transaction": 0.45, "analytics": 0.40, "track": 0.35, "fade": 0.35,
    "coalition": 0.30, "matchup": 0.40,
}


def _candidate_magnitude(c: dict) -> tuple[float, str, dict]:
    """Carry the signals the candidate already has instead of discarding them.

    A candidate arrives with a category, the facts that justified it, and
    sometimes a confidence note. None of that reached the ranker.
    """
    cat = (c.get("category") or "story").lower()
    base = CANDIDATE_BASE.get(cat, 0.35)
    facts = [f for f in (c.get("facts") or []) if f]
    # more supporting facts is weak evidence that more happened
    base += min(0.15, 0.05 * max(0, len(facts) - 1))
    extra: dict = {}
    if c.get("players"):
        extra["consequence"] = 0.25
        extra["consequence_label"] = "names a specific player"
    if (c.get("confidence") or "").strip():
        extra["rarity"] = 0.2
        extra["rarity_label"] = str(c["confidence"])[:60]
    label = f"{cat} signal"
    if facts:
        label += f", {len(facts)} supporting fact{'' if len(facts) == 1 else 's'}"
    return _clamp01(base), label, extra


def _candidate_items(storage: Storage, league: League, season: str, week: int,
                     *, drop_duplicates: bool = True) -> list[dict]:
    """The existing weekly Story Board, converted into inbox items. These are
    'what is true this week'; the diff items above are 'what changed'.

    Matchup candidates keep the Competitive Importance and Story Value they
    already carry: this layer is meant to coordinate the existing two-axis
    model rather than flatten it into a constant."""
    from leaguepage.editorial import load_coalitions
    from leaguepage.matchup_packet import compute_week
    from leaguepage.weekly_signals import weekly_story_candidates

    computed = compute_week(storage, league, week)
    if not computed:
        return []
    try:
        cands = weekly_story_candidates(storage, league, week, computed,
                                        coalitions=load_coalitions())
    except Exception:
        return []

    interest = {}
    for sc in computed.get("scored", []):
        ci_score = sc["competitive_importance"]["score"]
        sv_score = sc["story_value"]["score"]
        interest[sc["matchup"]["matchup_slug"]] = (ci_score, sv_score)

    out = []
    for c in cands:
        cid = c["candidate_id"]
        if drop_duplicates and cid.startswith(DUPLICATED_BY_DIFF):
            continue
        cat = {"result": "result", "matchup": "matchup", "force-flow": "transaction",
               "transaction": "transaction", "take": "receipt",
               "analytics": "standings"}.get(c.get("category"), c.get("category") or "story")
        magnitude, mag_label, extra = _candidate_magnitude(c)
        if cid.startswith("story:matchup:"):
            slug = cid.removeprefix("story:matchup:")
            ci_score, sv_score = interest.get(slug, (0, 0))
            magnitude = _clamp01((ci_score + sv_score) / 200.0)
            mag_label = f"CI {ci_score} + SV {sv_score} of 200"
            extra = {"consequence": _clamp01(ci_score / 100.0),
                     "consequence_label": f"competitive importance {ci_score}",
                     "history": _clamp01(sv_score / 200.0)}
        out.append({
            "item_id": cid, "category": cat,
            "headline": c["headline"],
            "what_changed": c.get("why") or "",
            "before": None, "after": None,
            "magnitude": magnitude, "magnitude_label": mag_label,
            "teams": c.get("teams") or [], "facts": c.get("facts") or [],
            "evidence": c.get("evidence") or [],
            "sections": c.get("recommended_sections") or ["lowdown"],
            "source": "candidate", "fresh": False, **extra,
        })
    return out


def as_candidates(items: list[dict]) -> list[dict]:
    """Inbox items in the shape the issue builders read.

    The inbox offered "Add to Issue" on every item and the decision reached
    nothing for a `change:*` id, because the builders iterate the story
    candidate list and a diff item was never in it. So a triaged upset
    appeared in no PREP.md, no AUTHORING file and no ghost brief, and the
    Commissioner re-typed by hand what he had already decided.
    """
    out = []
    for it in items:
        facts = list(it.get("facts") or [])
        if it.get("before") and it.get("after"):
            facts.insert(0, f"{it['before']} -> {it['after']}")
        why = it.get("matters") or it.get("what_changed") or ""
        out.append({
            "candidate_id": it["item_id"],
            "category": it.get("category") or "change",
            "headline": it.get("headline") or "",
            "teams": it.get("teams") or [],
            "players": it.get("players") or [],
            "facts": facts,
            "evidence": it.get("evidence") or [],
            "why": why,
            "recommended_sections": it.get("sections") or ["lowdown"],
            "confidence": (it.get("significance") or {}).get("band"),
        })
    return out


def saved_earlier(storage: Storage, league: League, season: str,
                  week: int, *, weeks_back: int = 5) -> dict[str, int]:
    """item_id -> the week it was saved, for anything saved and not since used.

    "Save for later" wrote a decision row under this week's key and nothing
    ever read it again, so later was never. An item can only come back while
    it is still true -- the decision row holds an id, not a story -- which is
    the honest limit: a saved item that has stopped being the case has
    stopped being a story.
    """
    saved: dict[str, int] = {}
    for back in range(weeks_back, 0, -1):
        wk = week - back
        if wk < 1:
            continue
        for cid, d in storage.get_story_decisions(
                league.slug, season, f"week-{wk:02d}").items():
            if d.get("decision") == "save":
                saved[cid] = wk
            elif d.get("decision") in ("include", "ignore"):
                saved.pop(cid, None)
    return saved


def _prior_lanes(storage: Storage, league: League, season: str,
                 week: int) -> dict[str, int]:
    """lane -> weeks since it was last INCLUDED in an issue. Drives the
    repetition penalty, so a running gag cools off instead of repeating."""
    from leaguepage import significance

    lanes: dict[str, int] = {}
    for back in range(1, 6):
        wk = week - back
        if wk < 1:
            break
        rows = storage.get_story_decisions(league.slug, season, f"week-{wk:02d}")
        for cid, d in rows.items():
            if d.get("decision") != "include":
                continue
            lanes.setdefault(significance.lane_of(cid), back)
    return lanes
