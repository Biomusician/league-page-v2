"""Rule-based draft story candidates.

Consumes draft_analysis output and emits scored, evidence-backed candidates
for the Story Board. Scores are editorial-interest heuristics (documented in
each candidate's `why`), never presented as objective quality judgments.

candidate_id values are stable across reruns — commissioner decisions key on
them.
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.draft_analysis import slugify
from leaguepage.editorial import confirmed_aliases

# K/DEF go "early" vs consensus ranks in every draft; a huge negative delta
# there is convention, not a story. Down-weighted, not hidden.
NOISY_POSITIONS = {"K", "DEF", "DST"}


def _cand(candidate_id: str, category: str, headline: str, *, teams=None, players=None,
          score: int = 50, why: str = "", facts=None, evidence_ids=None) -> dict:
    return {
        "candidate_id": candidate_id,
        "category": category,
        "headline": headline,
        "teams": teams or [],
        "players": players or [],
        "score": max(0, min(100, int(score))),
        "why": why,
        "facts": facts or [],
        "evidence": evidence_ids or [],
    }


def _delta_candidates(analysis: dict) -> list[dict]:
    """Reach/value candidates drawn from the full pick pool, skill positions
    first — K/DST deltas are surfaced separately at low score so their
    conventionally-huge deltas can't crowd out real stories."""
    out = []
    src = analysis.get("adp_provenance") or "no reference source"
    with_delta = [p for p in analysis.get("picks", []) if p["delta"] is not None]
    skill = [p for p in with_delta if (p.get("position") or "").upper() not in NOISY_POSITIONS]
    noisy = [p for p in with_delta if (p.get("position") or "").upper() in NOISY_POSITIONS]

    pools = [
        ("adp-reach", sorted((p for p in skill if p["delta"] < 0), key=lambda p: p["delta"])[:5], False),
        ("adp-value", sorted((p for p in skill if p["delta"] > 0), key=lambda p: -p["delta"])[:5], False),
        ("adp-reach", sorted((p for p in noisy if p["delta"] < 0), key=lambda p: p["delta"])[:2], True),
    ]
    for kind, pool, is_noisy in pools:
        for p in pool:
            magnitude = abs(p["delta"])
            base = min(25, 40 + magnitude / 2) if is_noisy else 40 + magnitude / 2
            direction = "ahead of" if p["delta"] < 0 else "after"
            why = (f"|delta| {magnitude:g} vs {src}"
                   + ("; down-weighted: K/DST timing vs consensus ranks is conventionally noisy" if is_noisy else ""))
            out.append(_cand(
                f"{kind}:{p['team_slug']}:{slugify(p['name'])}",
                kind,
                f"{p['name']} taken {magnitude:g} picks {direction} reference rank by {p['team_slug']}",
                teams=[p["team_slug"]], players=[p["name"]],
                score=base,
                why=why,
                facts=[f"Pick #{p['pick_no']}, reference rank {p['adp']:g} ({src}), delta {p['delta']:g}."],
                evidence_ids=p["evidence"],
            ))
    return out


def _strategy_candidates(analysis: dict) -> list[dict]:
    out = []
    teams = analysis.get("teams", [])
    for t in teams:
        for a in t.get("anomalies", []):
            metric = a["metric"]
            out.append(_cand(
                f"positional-strategy:{t['team_slug']}:{metric}",
                "positional-strategy",
                f"{t['team_slug']}: {a['fact']}",
                teams=[t["team_slug"]],
                score=62,
                why="Deviation of 3+ rounds from the league-median first pick at a core position.",
                facts=[a["fact"]],
                evidence_ids=t["evidence"] + [evidence.computed_ref(metric, analysis["league"], analysis["season"], t["team_slug"])],
            ))
        for s in t.get("stacks", []):
            n = len(s["partners"])
            out.append(_cand(
                f"stack:{t['team_slug']}:{slugify(s['qb'])}",
                "stack",
                f"{t['team_slug']} drafted a {s['nfl_team']} stack: {s['qb']} + {', '.join(s['partners'])}",
                teams=[t["team_slug"]], players=[s["qb"], *s["partners"]],
                score=45 + 15 * n,
                why=f"QB plus {n} drafted pass-catcher(s) from the same NFL offense.",
                facts=[f"All drafted by {t['team_slug']}; NFL team {s['nfl_team']}."],
                evidence_ids=s["evidence"],
            ))
        for c in t.get("nfl_team_concentration", []):
            out.append(_cand(
                f"nfl-concentration:{t['team_slug']}:{c['nfl_team'].lower()}",
                "nfl-concentration",
                f"{t['team_slug']} rosters {c['count']} players from {c['nfl_team']}",
                teams=[t["team_slug"]], players=c["players"],
                score=40 + 10 * (c["count"] - 3),
                why="Three or more drafted players from one NFL team concentrates weekly outcomes.",
                facts=[f"{c['count']} {c['nfl_team']} players: {', '.join(c['players'])}."],
                evidence_ids=c["evidence"],
            ))

    # strategy contrast: extremes of early-round RB vs WR appetite
    def early_count(t: dict, pos: str) -> int:
        return t.get("early_rounds_positions", {}).get(pos, 0)

    for pos in ("RB", "WR"):
        if len(teams) < 2:
            break
        most = max(teams, key=lambda t: early_count(t, pos))
        least = min(teams, key=lambda t: early_count(t, pos))
        hi, lo = early_count(most, pos), early_count(least, pos)
        if hi - lo >= 2 and most["team_slug"] != least["team_slug"]:
            out.append(_cand(
                f"strategy-contrast:{pos.lower()}:{most['team_slug']}:{least['team_slug']}",
                "strategy-contrast",
                f"{pos} appetite gap: {most['team_slug']} took {hi} {pos}s in rounds 1-3, {least['team_slug']} took {lo}",
                teams=[most["team_slug"], least["team_slug"]],
                score=55 + 5 * (hi - lo),
                why=f"Largest early-round {pos} investment gap in the league (rounds 1-3).",
                facts=[f"{most['team_slug']}: {hi} {pos}s in rounds 1-3; {least['team_slug']}: {lo}."],
                evidence_ids=most["evidence"] + least["evidence"],
            ))
    return out


def _archive_candidates(analysis: dict, storage, managers: dict[str, dict]) -> list[dict]:
    """Callbacks via the Story Memory layer: same-league archive only, unless
    a manager is explicitly marked allow_cross_league_callbacks (then labeled)."""
    from leaguepage.story_memory import retrieve_callbacks

    out = []
    for t in analysis.get("teams", []):
        hits = retrieve_callbacks(
            storage, analysis["league"], [t], managers,
            season=analysis["season"], limit=2,
        )
        for h in hits:
            label = f"{h['source_league']} {h['season'] or '????'}" + (f" wk{h['week']}" if h["week"] else "")
            cross = " CROSS-LEAGUE (explicitly approved); attribute to its source league." if h["cross_league"] else ""
            unreliable = " Source issue's season dating is not high-confidence; avoid date-specific claims." if h["date_unreliable"] else ""
            out.append(_cand(
                f"archive-callback:{t['team_slug']}:{h['issue_id']}",
                "archive-callback",
                f"Archive callback for {t['team_slug']}: '{h['matched_term']}' appears in {h['title']}",
                teams=[t["team_slug"]],
                score=48 if h["strength"] == "strong" else 40,
                why=f"Confirmed alias/team name found in this league's newsletter archive ({label})."
                    + cross + unreliable,
                facts=[f"Snippet: {h['snippet']}", f"Source: {h['title']} ({label})."],
                evidence_ids=[h["evidence"]] + t["evidence"],
            ))
    return out


def _coalition_candidates(analysis: dict, coalitions: dict) -> list[dict]:
    """Coalition angles surface ONLY from confirmed roster mappings.
    Inferred mappings stay invisible here by design."""
    out = []
    for c in coalitions.get("coalitions", []):
        mapping = c.get("roster_mapping") or {}
        if mapping.get("status") != "confirmed" or mapping.get("league") != analysis["league"]:
            continue
        team = next((t for t in analysis["teams"] if t["roster_id"] == mapping.get("roster_id")), None)
        if team is None:
            continue
        out.append(_cand(
            f"coalition-angle:{c['key']}:{team['team_slug']}",
            "coalition-angle",
            f"Coalition angle: {c['name']} ({team['team_slug']})",
            teams=[team["team_slug"]],
            score=70,
            why="Confirmed coalition team; nationality/aircraft/organizational material available.",
            facts=[f"Coalition {c['name']}: members {', '.join(c.get('members', []))}; tags {', '.join(c.get('tags', []))}."],
            evidence_ids=[evidence.coalition_ref(c["key"])] + team["evidence"],
        ))
    return out


def draft_story_candidates(
    analysis: dict,
    storage=None,
    managers: dict[str, dict] | None = None,
    coalitions: dict | None = None,
) -> list[dict]:
    if not analysis or not analysis.get("picks"):
        return []
    candidates = _delta_candidates(analysis) + _strategy_candidates(analysis)
    if storage is not None and managers:
        candidates += _archive_candidates(analysis, storage, managers)
    if coalitions:
        candidates += _coalition_candidates(analysis, coalitions)
    candidates.sort(key=lambda c: -c["score"])
    return candidates
