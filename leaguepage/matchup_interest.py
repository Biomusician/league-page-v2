"""Two-axis editorial-interest model: Competitive Importance and Story Value.

Both axes return a 0-100 score WITH the component breakdown that produced it,
so nothing reads as an oracle. The weights below are editorial preferences,
not objective truth — adjust freely; every component cites its evidence.

Prominence recommendation is transparent arithmetic over the two axes and the
commissioner can override any of it in matchup_state.
"""
from __future__ import annotations

from leaguepage.editorial import confirmed_coalition_mappings

# Adjustable editorial weights. Component points accumulate and clamp to 100.
WEIGHTS = {
    # competitive importance
    "top_table": 30,          # both teams in the top third of the standings
    "basement": 20,           # both teams in the bottom third
    "record_closeness": 20,   # identical or 1-game-apart records
    "projection_closeness": 25,  # only when a projection source exists
    "streak": 10,             # either team on a 3+ streak
    "late_season_leverage": 25,  # playoff-line implications (weeks played >= 6)
    "base_competitive": 25,   # every real matchup starts from here
    # story value
    "coalition": 35,          # confirmed coalition team involved
    "rivalry": 25,            # confirmed rivalry/relationship metadata
    "h2h_history": 15,        # prior meetings exist in-league
    "archive_callback": 15,   # a strong same-league (or approved) callback
    "trade_connection": 20,   # recent trade between the two rosters
    "roster_contrast": 15,    # opposed construction styles (from draft data)
    "open_take": 15,          # a tracked take involves either team
    "commissioner_flag": 30,  # commissioner marked this matchup interesting
    "base_story": 10,
}

PROMINENCE_LEVELS = ("FEATURE", "MAJOR", "STANDARD", "CAPSULE")
WORD_TARGETS = {"FEATURE": "250-400", "MAJOR": "125-200", "STANDARD": "75-125", "CAPSULE": "40-75"}


def _components_to_score(components: list[dict]) -> int:
    return min(100, sum(c["points"] for c in components))


def competitive_importance(matchup: dict, week_ctx: dict, weights: dict = WEIGHTS) -> dict:
    comps: list[dict] = [{"label": "baseline: a real head-to-head with standings at stake",
                          "points": weights["base_competitive"], "evidence": matchup["evidence"]}]
    total = week_ctx.get("total_teams") or 10
    third = max(1, round(total / 3))
    a, b = matchup["teams"]
    if a["standing"] <= third and b["standing"] <= third:
        comps.append({"label": f"top table: standings #{a['standing']} vs #{b['standing']}",
                      "points": weights["top_table"], "evidence": matchup["evidence"]})
    if a["standing"] > total - third and b["standing"] > total - third:
        comps.append({"label": f"basement: standings #{a['standing']} vs #{b['standing']}",
                      "points": weights["basement"], "evidence": matchup["evidence"]})
    ra, rb = a["record"], b["record"]
    if abs(ra["wins"] - rb["wins"]) <= 1 and week_ctx.get("weeks_played", 0) > 0:
        comps.append({"label": f"records within a game: {ra['wins']}-{ra['losses']} vs {rb['wins']}-{rb['losses']}",
                      "points": weights["record_closeness"], "evidence": matchup["evidence"]})
    proj = matchup.get("projection") or {}
    if proj.get("margin") is not None and abs(proj["margin"]) <= 5:
        comps.append({"label": f"projected within {abs(proj['margin']):g} ({proj.get('source')})",
                      "points": weights["projection_closeness"], "evidence": matchup["evidence"]})
    for t in (a, b):
        s = t.get("streak")
        if s and int(s[1:]) >= 3:
            comps.append({"label": f"{t['team_slug']} on a {s} streak",
                          "points": weights["streak"], "evidence": matchup["evidence"]})
            break
    return {"score": _components_to_score(comps), "components": comps}


def story_value(
    matchup: dict,
    *,
    coalitions: dict | None = None,
    story_memory: dict | None = None,
    draft_context: dict | None = None,
    commissioner_flagged: bool = False,
    weights: dict = WEIGHTS,
) -> dict:
    comps: list[dict] = [{"label": "baseline", "points": weights["base_story"],
                          "evidence": matchup["evidence"]}]
    roster_ids = {t["roster_id"] for t in matchup["teams"]}

    if coalitions:
        for c in confirmed_coalition_mappings(coalitions):
            m = c["roster_mapping"]
            if m.get("league") == matchup["league"] and m.get("roster_id") in roster_ids:
                comps.append({"label": f"confirmed coalition team in matchup: {c['name']}",
                              "points": weights["coalition"],
                              "evidence": [f"editorial:coalition:{c['key']}"]})
        for rel in coalitions.get("relationships", []):
            if rel.get("status") != "confirmed":
                continue
            mapped = {c["key"]: c["roster_mapping"].get("roster_id")
                      for c in confirmed_coalition_mappings(coalitions)}
            sides = [mapped.get(k) for k in rel.get("between", [])]
            if all(s in roster_ids for s in sides if s is not None) and len([s for s in sides if s]) == 2:
                comps.append({"label": f"confirmed rivalry: {' vs '.join(rel['between'])} ({rel['type']})",
                              "points": weights["rivalry"],
                              "evidence": [f"editorial:coalition:{k}" for k in rel["between"]]})

    if (matchup.get("h2h") or {}).get("meetings"):
        rec = matchup["h2h"]["record"]
        comps.append({"label": f"prior meetings on record: {rec}",
                      "points": weights["h2h_history"], "evidence": matchup["evidence"]})

    sm = story_memory or {}
    strong = [c for c in sm.get("callbacks", []) if c.get("strength") == "strong"]
    if strong:
        comps.append({"label": f"strong archive callback available: {strong[0]['title']}",
                      "points": weights["archive_callback"],
                      "evidence": [strong[0]["evidence"]]})
    if sm.get("takes"):
        comps.append({"label": f"{len(sm['takes'])} tracked take(s) involve these teams",
                      "points": weights["open_take"],
                      "evidence": [f"take:{t['take_id']}" for t in sm["takes"]]})

    for tx in matchup.get("recent_transactions", []):
        if tx.get("type") == "trade":
            comps.append({"label": f"recent trade touching this matchup (week {tx['week']})",
                          "points": weights["trade_connection"], "evidence": tx["evidence"]})
            break

    if draft_context:
        slugs = [t["team_slug"] for t in matchup["teams"]]
        mixes = [draft_context.get(s, {}).get("early_rounds_positions") for s in slugs]
        if all(mixes):
            rb_gap = abs(mixes[0].get("RB", 0) - mixes[1].get("RB", 0))
            wr_gap = abs(mixes[0].get("WR", 0) - mixes[1].get("WR", 0))
            if max(rb_gap, wr_gap) >= 2:
                comps.append({"label": f"opposed early-draft construction (rounds 1-3 mix {mixes[0]} vs {mixes[1]})",
                              "points": weights["roster_contrast"],
                              "evidence": matchup["evidence"]})

    if commissioner_flagged:
        comps.append({"label": "commissioner flagged this matchup",
                      "points": weights["commissioner_flag"], "evidence": []})

    return {"score": _components_to_score(comps), "components": comps}


def classify(matchup: dict, ci: dict, sv: dict, week_ctx: dict) -> list[str]:
    tags = []
    labels = " | ".join(c["label"] for c in ci["components"] + sv["components"])
    if "top table" in labels:
        tags.append("Top Table")
    if "basement" in labels:
        tags.append("Basement Brawl")
    if "coalition team in matchup" in labels:
        tags.append("Coalition Warfare")
    if "confirmed rivalry" in labels:
        tags.append("Rivalry")
    if "recent trade" in labels:
        tags.append("Revenge Game")
    if "projected within" in labels:
        tags.append("Photo Finish candidate")
    if week_ctx.get("weeks_played", 0) >= 6 and "leverage" in labels:
        tags.append("Playoff Leverage")
    return tags


def recommend_prominence(scored: list[dict]) -> None:
    """Mutates: adds recommended_prominence. Transparent rule — best combined
    score is the FEATURE, next two MAJOR, remainder STANDARD, and anything
    past six matchups CAPSULE. Commissioner override always wins downstream."""
    ranked = sorted(scored, key=lambda m: -(m["competitive_importance"]["score"] + m["story_value"]["score"]))
    for i, m in enumerate(ranked):
        if i == 0:
            m["recommended_prominence"] = "FEATURE"
        elif i <= 2:
            m["recommended_prominence"] = "MAJOR"
        elif i <= 5:
            m["recommended_prominence"] = "STANDARD"
        else:
            m["recommended_prominence"] = "CAPSULE"
