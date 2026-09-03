"""Deterministic fallbacks: Scout View and the Model Board.

A public tab that answers a click with "Preview pending." teaches a reader
that the site is unfinished, and they stop clicking. Every primary route
should have something real behind it — but the fix cannot be to fill the
gap with machine-written prose in the Commissioner's voice, because then
his actual writing is worth nothing.

So these are scaffolding, and they say so:

* **Scout View** — why a matchup is worth watching, in facts. Positional
  contrast, head-to-head, recent moves, coalition and draft context. It
  appears only when no approved Commissioner preview exists, and is replaced
  the moment one does.
* **Model Board** — a deterministic ranking with a tier, a strongest room, a
  weakness and one explanatory factor. Labelled "Model view · Commissioner
  rankings not yet published". When his rankings do exist, the model stays
  as a comparison column, because the disagreement is the fun part.

Neither ever tells a joke, assesses a person, or predicts a winner. They
report structure. Anything with a personality on this site was typed by a
human.
"""
from __future__ import annotations

from leaguepage.draft_value import SKILL_POSITIONS

MIN_ROOM_GAP = 3            # rooms closer than this are not a contrast
MAX_SCOUT_POINTS = 4
MODEL_TIERS = (
    (0.25, "Peer Competition"),
    (0.50, "Near-Peer Competition"),
    (0.75, "Competitive but Flawed"),
    (1.01, "Strategic Reassessment Required"),
)


# ------------------------------------------------------------ Scout View


def _room_contrasts(profile: dict, rid_a: int, rid_b: int,
                    name_a: str, name_b: str) -> list[str]:
    if not profile:
        return []
    gaps = []
    for pos in profile["positions"]:
        if pos not in SKILL_POSITIONS:
            continue        # a kicker gap is not why anyone watches a game
        ra, rb = profile["ranks"][pos][rid_a], profile["ranks"][pos][rid_b]
        gaps.append((abs(ra - rb), pos, ra, rb))
    gaps.sort(reverse=True)
    out = []
    for gap, pos, ra, rb in gaps[:2]:
        if gap >= MIN_ROOM_GAP:
            lead_name, lead_rank, trail_name, trail_rank = (
                (name_a, ra, name_b, rb) if ra < rb else (name_b, rb, name_a, ra))
            out.append(f"{pos}: {lead_name} #{lead_rank} against "
                       f"{trail_name} #{trail_rank} of {profile['n']}")
    return out


def scout_view(matchup: dict, *, profile: dict | None, names: dict[int, str],
               tags: list[str], moves_by_rid: dict[int, list[dict]],
               recap_by_rid: dict[int, dict] | None = None) -> dict | None:
    """Facts that make one matchup worth opening. None when there are none.

    Returning None matters as much as returning content: a matchup between
    two mid-table teams with no history, no moves and no roster contrast is
    genuinely unremarkable, and pretending otherwise is the thing this
    module exists to avoid."""
    a, b = matchup["teams"]
    rid_a, rid_b = a["roster_id"], b["roster_id"]
    name_a = names.get(rid_a, f"Roster {rid_a}")
    name_b = names.get(rid_b, f"Roster {rid_b}")

    why: list[str] = []
    for line in _room_contrasts(profile, rid_a, rid_b, name_a, name_b):
        why.append(line)
    if profile:
        for rid, nm in ((rid_a, name_a), (rid_b, name_b)):
            skill = [p for p in profile["positions"] if p in SKILL_POSITIONS]
            if not skill:
                continue
            best = min(skill, key=lambda p: profile["ranks"][p][rid])
            rank = profile["ranks"][best][rid]
            if rank <= max(1, round(0.25 * profile["n"])):
                why.append(f"{nm} brings a top room: {best} #{rank} of "
                           f"{profile['n']}")
    for tag in tags:
        if tag in ("Coalition Warfare", "Rivalry", "Revenge Game", "Top Table",
                   "Basement Brawl", "Playoff Leverage", "Seeding at Stake"):
            why.append(_TAG_NOTES[tag])

    watch: list[str] = []
    h2h = matchup.get("h2h") or {}
    rec = h2h.get("record") or {}
    if sum(rec.values() or [0]) >= 1:
        watch.append(f"Head to head in this league: {name_a} "
                     f"{rec.get(rid_a, 0)}–{rec.get(rid_b, 0)} {name_b}.")
    last = h2h.get("last_meeting")
    if last:
        pts = last["points"]
        watch.append(f"Last meeting, week {last['week']}: "
                     f"{pts.get(rid_a, 0):g} – {pts.get(rid_b, 0):g}.")
    for rid, nm in ((rid_a, name_a), (rid_b, name_b)):
        for mv in (moves_by_rid.get(rid) or [])[-1:]:
            flag = " (flagged questionable)" if mv.get("questionable") else ""
            watch.append(f"{nm}, week {mv['week']}: {mv['line']}{flag}.")
    for rid, nm in ((rid_a, name_a), (rid_b, name_b)):
        recap = (recap_by_rid or {}).get(rid) or {}
        pick = recap.get("biggest_reach") or recap.get("biggest_steal")
        if pick:
            watch.append(f"{nm} drafted {pick['name']} — {pick['dv']['label']} "
                         "against the consensus board.")

    why, watch = why[:MAX_SCOUT_POINTS], watch[:MAX_SCOUT_POINTS]
    if len(why) + len(watch) < 2:
        return None
    return {
        "why": why,
        "watch": watch,
        "note": ("Scout View is computed from synced data — roster "
                 "construction, league history and transactions. It is not a "
                 "prediction and it is not the Commissioner's preview."),
    }


_TAG_NOTES = {
    # The tag fires when EITHER side is a coalition team, so calling it
    # warfare described a coalition-versus-coalition game that was not
    # happening. And "the standing alliance context applies" is a sentence
    # with no content in it.
    "Coalition Warfare": "A coalition team is involved, which carries its own history.",
    "Rivalry": "Confirmed rivalry in this league's history.",
    "Revenge Game": "These two have traded with each other.",
    "Top Table": "Both sides are in the top of the table.",
    "Basement Brawl": "Both sides are in the bottom of the table.",
    # The tag fires on standings position, which is not the same claim as
    # the simulation's. Say what was measured; the leverage numbers beside
    # it say what it is worth.
    "Playoff Leverage": "At least one side is sitting on the playoff cutline.",
    "Seeding at Stake": "Both sides already hold berths; this is about seeding.",
}


# ----------------------------------------------------------- Model Board


def _tier(index: int, n: int) -> str:
    q = (index + 1) / max(1, n)
    for threshold, label in MODEL_TIERS:
        if q <= threshold:
            return label
    return MODEL_TIERS[-1][1]


def model_board(*, profile: dict, names: dict[int, str], slugs: dict[int, str],
                standings: list[dict], form: dict[int, dict] | None,
                weeks_played: int, source: str | None = None) -> dict:
    """A ranking the site can always show, with its reasoning printed.

    Preseason it is roster construction alone. Once games exist, results
    carry the larger share — a model that still ranks on August rosters in
    November is worse than no model."""
    if not profile or not profile.get("teams"):
        return {"rows": [], "basis": None}
    n = profile["n"]
    skill = [p for p in profile["positions"] if p in SKILL_POSITIONS]
    weight_results = min(0.7, 0.12 * weeks_played)   # 0 preseason, 0.7 by week 6

    pf_rank = {row["roster_id"]: i + 1
               for i, row in enumerate(sorted(standings,
                                              key=lambda r: -float(r.get("pf") or 0)))}
    scored = []
    for rid in profile["teams"]:
        construction = sum(profile["ranks"][p][rid] for p in skill) / max(1, len(skill))
        results = pf_rank.get(rid, (n + 1) / 2)
        score = construction * (1 - weight_results) + results * weight_results
        scored.append((score, rid, construction, results))
    scored.sort(key=lambda x: (x[0], x[1]))

    # Two teams can land on the same score, and the sort then breaks the tie
    # on roster_id: a number that cannot order the board should not be
    # presented as though it had. Say so on the row.
    tied = {s for s, c in
            __import__("collections").Counter(x[0] for x in scored).items()
            if c > 1}

    rows = []
    for i, (score, rid, construction, results) in enumerate(scored):
        best = min(skill, key=lambda p: profile["ranks"][p][rid]) if skill else None
        worst = max(skill, key=lambda p: profile["ranks"][p][rid]) if skill else None
        # A team whose skill rooms all rank alike had one room printed as
        # both what carries it and what exposes it. Nothing separates them,
        # so name neither.
        if best and profile["ranks"][best][rid] == profile["ranks"][worst][rid]:
            best = worst = None
        factor = None
        if weeks_played and rid in (form or {}):
            f = form[rid]
            factor = f"#{f['rank']} scoring over the last {f['window_label']}"
        elif best and worst:
            factor = (f"average skill-room rank {construction:.1f} of {n}; "
                      f"{best} carries it, {worst} is the exposure")
        rec = next((s for s in standings if s["roster_id"] == rid), {})
        rows.append({
            "rank": i + 1,
            "roster_id": rid,
            "name": names.get(rid, f"Roster {rid}"),
            "slug": slugs.get(rid),
            "tier": _tier(i, n),
            "strongest": (f"{best} #{profile['ranks'][best][rid]} of {n}"
                          if best else None),
            "weakest": (f"{worst} #{profile['ranks'][worst][rid]} of {n}"
                        if worst else None),
            "factor": factor,
            "tied": score in tied,
            "record": (f"{rec.get('wins', 0)}-{rec.get('losses', 0)}"
                       if weeks_played else None),
        })
    # Every number on this board traces back to one reference board, and the
    # page used to say "computed from roster construction and nothing else"
    # without ever naming it. A stat without provenance is not publishable
    # here, and this one was printed on five pages.
    src = f" Room strength is measured against {source}." if source else ""
    basis = ((f"Skill-position room strength only ({', '.join(skill)}); no games "
              f"played yet.{src}") if not weeks_played else
             (f"{int((1 - weight_results) * 100)}% roster construction, "
              f"{int(weight_results * 100)}% scoring through week "
              f"{weeks_played}.{src}"))
    return {"rows": rows, "basis": basis, "weeks_played": weeks_played}


def compare_to_commissioner(model: dict, ranking: list[dict]) -> list[dict]:
    """Attach the model's rank to the Commissioner's published ranking.

    Where they disagree is the interesting part of the page, so the
    disagreement is printed as a signed number rather than hidden."""
    by_rid = {r["roster_id"]: r for r in model.get("rows", [])}
    out = []
    for row in ranking:
        m = by_rid.get(row.get("roster_id"))
        gap = (m["rank"] - row["rank"]) if m and row.get("rank") else None
        out.append({**row, "model_rank": m["rank"] if m else None,
                    "model_gap": gap,
                    "model_gap_label": (None if not gap else
                                        f"model has them {abs(gap)} spot"
                                        f"{'' if abs(gap) == 1 else 's'} "
                                        f"{'lower' if gap > 0 else 'higher'}")})
    return out


# ------------------------------------------------------------ Black Box


def black_box_preview(*, profile: dict, names: dict[int, str],
                      reaches: list[dict], steals: list[dict],
                      weeks_played: int) -> list[dict]:
    """What the Black Box is watching for before it has any records.

    Not manufactured content: these are the extremes already computed
    elsewhere, framed as the marks this season will be measured against."""
    if weeks_played >= 3 or not profile:
        return []
    out = []
    skill = [p for p in profile["positions"] if p in SKILL_POSITIONS]
    for pos in skill:
        ranked = profile["ranks"][pos]
        rid = min(ranked, key=ranked.get)
        out.append({"label": f"Deepest {pos} room on paper",
                    "value": names.get(rid, f"Roster {rid}"),
                    "note": f"#1 of {profile['n']} against this league's lineup demand"})
    if reaches:
        p = reaches[0]
        out.append({"label": "Largest departure from consensus",
                    "value": f"{p['name']} — {p['team']}",
                    "note": p["dv"]["label"]})
    if steals:
        p = steals[0]
        out.append({"label": "Largest value on the board",
                    "value": f"{p['name']} — {p['team']}",
                    "note": p["dv"]["label"]})
    return out[:6]
