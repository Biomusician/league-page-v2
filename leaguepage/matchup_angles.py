"""Matchup story angles — 3-5 genuinely different premises per matchup.

Each angle comes from a distinct family so the set never collapses into five
versions of one idea. Premises are deterministic and factual; strength is a
label (strong / medium / speculative), never a number, because it is an
editorial guess. Collision warnings come from the editorial_usage log so a
funny idea doesn't get run into the ground; they warn rather than prohibit.

Coalition angles appear only for CONFIRMED coalition roster mappings, and the
coalition lanes rotate (command relationships, fighter culture, Rafale vs
Gripen, operator-vs-maintainer, procurement, alliance politics,
interoperability, capability jokes, pilots-make-MX-work, multinational
command dysfunction) with recently-used lanes flagged.
"""
from __future__ import annotations

from leaguepage.editorial import confirmed_coalition_mappings
from leaguepage.storage import Storage

COALITION_LANES = [
    "coalition-command-relationships",
    "fighter-culture",
    "rafale-vs-gripen",
    "operator-vs-maintainer",
    "procurement-comparison",
    "alliance-politics",
    "interoperability",
    "capability-stereotypes",
    "pilots-create-mx-work",
    "multinational-command-dysfunction",
]

LEAGUE_FRAMES = {
    "disco": ("CRC battle-management", "Narrate the matchup as an air battle on the scope: "
              "tracks, commit criteria, a clean or dirty picture, the ATO."),
    "surfeit": ("Force Design 2035", "Apply a real force-design concept accurately: Agile "
                "Combat Employment, contested logistics, kill webs, multi-capable Airmen, "
                "mission command, attritable assets."),
}


def _collision_warnings(storage: Storage, league: str, season: str, week: int,
                        values: list[str], kind: str) -> list[str]:
    warnings = []
    for use in storage.recent_editorial_usage(league, season, since_week=max(1, week - 3), kind=kind):
        if use["value"] in values:
            warnings.append(
                f"'{use['value']}' used week {use['week']}"
                + (f" ({use['note']})" if use.get("note") else "")
                + " — avoid or reuse deliberately."
            )
    return warnings


def generate_angles(
    storage: Storage,
    matchup: dict,
    story_memory: dict,
    *,
    coalitions: dict | None = None,
    draft_context: dict | None = None,
) -> list[dict]:
    league, season, week = matchup["league"], matchup["season"], matchup["week"]
    a, b = matchup["teams"]
    slug = matchup["matchup_slug"]
    angles: list[dict] = []

    def add(angle_id: str, family: str, title: str, premise: str, *,
            ev: list[str], why: str, strength: str, callback: dict | None = None,
            collision_kind: str | None = None, collision_values: list[str] | None = None):
        warnings = []
        if collision_kind and collision_values:
            warnings = _collision_warnings(storage, league, season, week, collision_values, collision_kind)
        warnings += _collision_warnings(storage, league, season, week, [family], "frame")
        angles.append({
            "angle_id": f"{slug}:{angle_id}",
            "family": family,
            "title": title,
            "premise": premise,
            "evidence": ev,
            "why": why,
            "strength": strength,
            "collision_warnings": warnings,
            "callback": callback,
        })

    # 1. competitive/standings frame — always available
    rec_a, rec_b = a["record"], b["record"]
    played = (rec_a["wins"] + rec_a["losses"]) > 0
    if played:
        premise = (f"Standings stakes: #{a['standing']} ({rec_a['wins']}-{rec_a['losses']}) meets "
                   f"#{b['standing']} ({rec_b['wins']}-{rec_b['losses']}); frame the week around "
                   "what the result does to the table.")
        strength = "strong" if abs(a["standing"] - b["standing"]) <= 2 else "medium"
    else:
        premise = ("Season opener: no records yet, so the competitive frame is expectation-"
                   "setting — what each side built to do, and what Week 1 will actually test first.")
        strength = "medium"
    add("competitive", "competitive", "The standings frame", premise,
        ev=matchup["evidence"], why="Always grounded; strongest when the table is close.",
        strength=strength)

    # 2. roster-construction frame — needs draft context
    if draft_context:
        da = draft_context.get(a["team_slug"]) or {}
        db = draft_context.get(b["team_slug"]) or {}
        if da and db:
            from leaguepage.config import get_league
            from leaguepage.matchup_interest import format_position_mix
            from leaguepage.team_names import resolve_public_names

            try:
                resolved = resolve_public_names(storage, get_league(league))
            except Exception:
                resolved = {}

            def _nm(t: dict) -> str:
                pub = (resolved.get(t.get("roster_id")) or {}).get("name")
                return pub or t.get("team_name") or t["team_slug"]

            name_a, name_b = _nm(a), _nm(b)
            premise = (f"Construction contrast: {name_a} opened its draft "
                       f"{format_position_mix(da.get('early_rounds_positions'))} "
                       f"against {name_b}'s "
                       f"{format_position_mix(db.get('early_rounds_positions'))}; "
                       "the matchup is a live test of two build philosophies.")
            add("construction", "roster-construction", "Two build philosophies collide", premise,
                ev=matchup["evidence"] + [f"computed:early-position-concentration:{league}:{season}:{a['team_slug']}",
                                          f"computed:early-position-concentration:{league}:{season}:{b['team_slug']}"],
                why="Draft-derived; strongest in early weeks before results overwrite draft narratives.",
                strength="strong" if not played else "medium")

    # 3. historical/rivalry frame — only when history exists
    h2h = matchup.get("h2h") or {}
    best_callback = next((c for c in story_memory.get("callbacks", []) if c["strength"] == "strong"), None)
    if h2h.get("meetings") or best_callback:
        bits = []
        ev = list(matchup["evidence"])
        if h2h.get("meetings"):
            bits.append(f"H2H record {h2h['record']}")
        if best_callback:
            bits.append(f"archive callback: {best_callback['title']}")
            ev.append(best_callback["evidence"])
        add("history", "history", "The grudge ledger",
            f"History frame: {'; '.join(bits)}. Open on the past, cash it out against this week.",
            ev=ev, why="Only offered because real history exists.",
            strength="strong" if h2h.get("meetings") else "medium",
            callback=best_callback,
            collision_kind="callback",
            collision_values=[best_callback["evidence"]] if best_callback else [])

    # 4. Air Force / work frame — league-themed; coalition variant when confirmed
    frame_name, frame_hint = LEAGUE_FRAMES.get(league, ("service", "Service-life analogy."))
    coalition_here = []
    if coalitions:
        roster_ids = {a["roster_id"], b["roster_id"]}
        coalition_here = [c for c in confirmed_coalition_mappings(coalitions)
                          if c["roster_mapping"].get("league") == league
                          and c["roster_mapping"].get("roster_id") in roster_ids]
    if coalition_here:
        c = coalition_here[0]
        used_lanes = {u["value"] for u in storage.recent_editorial_usage(
            league, season, since_week=max(1, week - 4), kind="joke_family")}
        fresh = [lane for lane in COALITION_LANES if lane not in used_lanes]
        lane = fresh[0] if fresh else COALITION_LANES[0]
        add("coalition", "service-frame", f"Coalition Warfare: {c['name']}",
            f"Coalition frame via the '{lane}' lane (tags: {', '.join(c.get('tags', []))}). "
            "Ground the joke in a recognizable aviation/organizational fact, and rotate off "
            "recently used lanes.",
            ev=[f"editorial:coalition:{c['key']}"] + matchup["evidence"],
            why="Confirmed coalition team in the matchup; lanes rotated via the usage log.",
            strength="strong",
            collision_kind="joke_family", collision_values=COALITION_LANES)
    else:
        add("service", "service-frame", f"The {frame_name} frame",
            f"{frame_hint} Pick one concept and pay it off against this specific matchup; "
            "no buzzword soup.",
            ev=matchup["evidence"],
            why=f"League theme ({frame_name}); audience takes the jargon unglossed.",
            strength="medium")

    # 5. wildcard/pop-history frame — always offered, clearly speculative
    add("wildcard", "wildcard", "The wildcard frame",
        f"Open on an extended non-football analogy (military history, pop culture, shared "
        f"service experience) chosen for {a['team_slug']} vs {b['team_slug']}, developed and "
        "paid off against the game per the voice profile's one-analogy rule.",
        ev=matchup["evidence"],
        why="The archive's most-loved openers are this move; premise is writer's choice.",
        strength="speculative")

    return angles
