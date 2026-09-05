"""Which transactions were actually worth noticing, and the evidence why.

`transaction_analysis` already answers "what happened and what might have
motivated it". This answers a narrower question the Commissioner asks every
week: out of forty routine adds and drops, which four are worth a sentence?

Two rules shape it.

**League-relative, not hardcoded.** "A big FAAB bid" is not 15% of budget in
the abstract; it is a bid this league would find large. So the spend flags
read the distribution of this league's own completed bids and fire on
outliers within it. A league where nobody bids more than $3 gets a
different bar from one where $40 is routine, and neither bar is a number
somebody typed once.

**Every flag carries its evidence, and inference is labelled.** A flag says
what it saw -- the bid, the rank, the roster hole -- so the Commissioner can
disagree with it. Where a flag is a reading rather than an observation, its
`inferred` field says so, and the wording never states a motive anyone
would have to be a mind reader to know. Internally a move can be called
questionable. In public the evidence goes out and the Commissioner's own
voice does any roasting.

Nothing here publishes on its own. Flags are private research; the page
publishes through the ordinary explicit build-and-deploy, and a Commissioner
note is optional everywhere.
"""
from __future__ import annotations

from statistics import median

from leaguepage.config import League
from leaguepage.storage import Storage

# A pickup nobody paid for that the reference board rates as a starter.
STARTER_VALUE = 100.0        # ~ reference rank 150 or better
USABLE_VALUE = 60.0          # ~ reference rank 190; the replacement line
# A bid is an outlier when it is this many times the league's own median
# non-zero bid, and at least this share of a budget. Both, so a league of
# $1 bids does not make a $3 claim "unusual".
BID_OUTLIER_MULTIPLE = 3.0
BID_OUTLIER_FLOOR = 0.10
# Bottom of the league at a position, for "this team actually needed one".
NEED_QUANTILE = 0.75         # rank in the worst quarter
CHURN_WEEKS = 2              # added and gone again inside this many weeks


def _label(flag: str) -> str:
    return {
        "faab-spike": "Unusual FAAB spend",
        "faab-top": "Biggest bid of the season so far",
        "notable-drop": "Let go of someone useful",
        "questionable-add": "Hard to read",
        "blocking-add": "Looks like a block",
        "valuable-pickup": "Free starter",
        "trade": "Trade",
        "churn": "Added and gone again",
    }.get(flag, flag)


def _bid_stats(rows: list[dict]) -> dict:
    """What this league's bidding actually looks like."""
    bids = [r["faab"] for r in rows if r.get("faab")]
    shares = [r["faab_share"] for r in rows if r.get("faab") and r.get("faab_share")]
    return {
        "n": len(bids),
        "median": median(bids) if bids else 0.0,
        "max": max(bids) if bids else 0.0,
        "median_share": median(shares) if shares else 0.0,
    }


def _need_ranks(profile: dict, pos: str) -> tuple[dict, int]:
    return (profile.get("ranks") or {}).get(pos, {}), profile.get("n") or 0


def _teams_needing(profile: dict, pos: str, exclude: set[int]) -> list[int]:
    """Teams in the worst quarter of the league at this position.

    Only rooms the ranking actually measured: a position where every team
    scores zero ranks by roster id, and "worst quarter" of that is a fact
    about the sort order, not about anybody's roster.
    """
    from leaguepage.team_analytics import is_rated

    ranks, n = _need_ranks(profile, pos)
    if not ranks or not n:
        return []
    cut = max(1, int(n * NEED_QUANTILE))
    return sorted(rid for rid, rank in ranks.items()
                  if rank >= cut and rid not in exclude and is_rated(profile, pos, rid))


def flags_for(row: dict, *, stats: dict, values: dict, profile: dict,
              names: dict[int, str], started: set[str],
              later_dropped: dict[str, int]) -> list[dict]:
    """Every flag this one transaction earns, with what earned it."""
    out: list[dict] = []
    rids = set(row.get("rids") or [])
    adds = row.get("adds") or []
    drops = row.get("drops") or []

    def value_of(p):
        v = values.get(p.get("pid")) or {}
        return float(v.get("value") or 0.0)

    if row.get("type") == "trade":
        out.append({
            "flag": "trade", "label": _label("trade"), "inferred": False,
            "why": "A trade. Two managers agreed something, which is rarer "
                   "than a waiver claim and always worth a look.",
            "evidence": [f"{len(adds)} player(s) moved between "
                         f"{len(rids)} roster(s)"],
        })

    bid = row.get("faab") or 0
    share = row.get("faab_share") or 0.0
    if bid:
        if stats["max"] and bid >= stats["max"] and share >= BID_OUTLIER_FLOOR:
            out.append({
                "flag": "faab-top", "label": _label("faab-top"), "inferred": False,
                "why": "Nothing has cost more in this league this season.",
                "evidence": [f"{bid} FAAB, {round(share * 100)}% of a budget",
                             f"league median bid so far: {stats['median']:g}"],
            })
        elif (stats["median"] and bid >= stats["median"] * BID_OUTLIER_MULTIPLE
                and share >= BID_OUTLIER_FLOOR):
            out.append({
                "flag": "faab-spike", "label": _label("faab-spike"), "inferred": False,
                "why": "Well outside what this league normally pays.",
                "evidence": [f"{bid} FAAB, {round(share * 100)}% of a budget",
                             f"league median bid: {stats['median']:g} "
                             f"across {stats['n']} paid claim(s)"],
            })

    for p in adds:
        v = value_of(p)
        if v >= STARTER_VALUE and not bid:
            out.append({
                "flag": "valuable-pickup", "label": _label("valuable-pickup"),
                "inferred": False,
                "why": f"{p['name']} is rated a starter and cost nothing.",
                "evidence": [f"{p['name']} ({p.get('position') or '?'})",
                             "claimed with no FAAB committed"],
            })
        # Churn is one roster adding a player and letting him go again. The
        # same player leaving another team's bench in the same week is the
        # ordinary way a waiver wire works, not churn.
        gone = later_dropped.get((p.get("rid"), p.get("pid")))
        if gone is not None and 0 <= gone - row.get("week", 0) <= CHURN_WEEKS:
            span = gone - row.get("week", 0)
            when = "the same week" if span == 0 else f"{span} week(s) later"
            out.append({
                "flag": "churn", "label": _label("churn"), "inferred": False,
                "why": f"{p['name']} was added and dropped again {when}.",
                "evidence": [f"added week {row.get('week', 0)}, dropped week {gone}"],
            })

    for p in drops:
        v = value_of(p)
        if v >= STARTER_VALUE:
            out.append({
                "flag": "notable-drop", "label": _label("notable-drop"),
                "inferred": False,
                "why": f"{p['name']} rates as a starter and is now on nobody's "
                       f"roster or somebody else's.",
                "evidence": [f"{p['name']} ({p.get('position') or '?'}) "
                             f"released"],
            })

    # A reading, not an observation: the adder was already strong at the
    # position, did not start the player, and somebody else is short there.
    for p in adds:
        pos = (p.get("position") or "").upper()
        if not pos or p.get("pid") in started:
            continue
        if value_of(p) < USABLE_VALUE:
            continue          # denying somebody a player nobody starts is not a block
        ranks, n = _need_ranks(profile, pos)
        if not ranks or not n:
            continue
        mine = min((ranks.get(rid, n) for rid in rids), default=n)
        if mine > 3:
            continue                      # they were not already deep there
        short = _teams_needing(profile, pos, exclude=rids)
        if not short:
            continue
        out.append({
            "flag": "blocking-add", "label": _label("blocking-add"),
            "inferred": True,
            "why": (f"They were already strong at {pos}, did not start "
                    f"{p['name']}, and {len(short)} team(s) are in the bottom "
                    f"quarter of the league there. That is what a block looks "
                    f"like from the outside; it is not proof of one."),
            "evidence": [f"their {pos} room ranks #{mine} of {n}",
                         f"short at {pos}: "
                         + ", ".join(names.get(r, f"Roster {r}") for r in short[:3])],
        })

    if (row.get("rationale") or {}).get("kind") == "questionable":
        out.append({
            "flag": "questionable-add", "label": _label("questionable-add"),
            "inferred": True,
            "why": row["rationale"].get("text")
                   or "The engine could not find a reading that fits.",
            "evidence": ["rationale engine: questionable"],
        })
    return out


def review(storage: Storage, league: League, season: str, through_week: int,
           *, names: dict[int, str] | None = None) -> list[dict]:
    """Flagged transactions for this league, worst-first, with evidence.

    Private. This is the Commissioner's review queue: what the week did that
    was not routine, why each one surfaced, and his own note if he left one.
    """
    from leaguepage.matchup_analysis import weekly_scores
    from leaguepage.team_analytics import player_values, positional_profile
    from leaguepage.team_names import resolve_public_names
    from leaguepage.transaction_analysis import analyze_transactions, describe_move

    rows = analyze_transactions(storage, league, through_week)
    if not rows:
        return []
    if names is None:
        names = {rid: (v.get("name") or f"Roster {rid}")
                 for rid, v in resolve_public_names(storage, league).items()}
    played = len(weekly_scores(storage, league.league_id, through_week) or {})
    values, _stage = player_values(storage, league, weeks_played=played)
    profile = positional_profile(storage, league, weeks_played=played)
    stats = _bid_stats(rows)

    # Who actually took the field, and who was dropped again later.
    started: set[str] = set()
    for wk in range(1, through_week + 1):
        for m in storage.get_matchups(league.league_id, wk):
            started.update(p for p in (m.get("starters") or []) if p)
    later_dropped: dict[tuple[int | None, str], int] = {}
    for r in sorted(rows, key=lambda r: r["week"]):
        for p in (r.get("drops") or []):
            later_dropped.setdefault((p.get("rid"), p["pid"]), r["week"])

    notes = storage.force_flow_notes(league.slug, season)
    out = []
    for row in rows:
        flags = flags_for(row, stats=stats, values=values, profile=profile,
                          names=names, started=started, later_dropped=later_dropped)
        if not flags:
            continue
        out.append({
            "txn_id": row["txn_id"],
            "week": row["week"],
            "type": row["type"],
            "line": describe_move(row),
            "teams": [names.get(r, f"Roster {r}") for r in row.get("rids", [])],
            "faab": row.get("faab"),
            "flags": flags,
            "note": (notes.get(row["txn_id"]) or {}).get("note"),
            # Provenance for anything published off the back of this: it is
            # arithmetic over synced data, not a language model.
            "evidence_ref": f"sleeper:transaction:{row['txn_id']}",
        })
    out.sort(key=lambda r: (-r["week"], -len(r["flags"])))
    return out
