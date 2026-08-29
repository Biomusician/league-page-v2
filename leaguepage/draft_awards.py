"""Draft award NOMINATIONS — never automatic winners.

Objective categories rank candidates on a stated metric; subjective ones list
evidence-backed nominees with no fake numerical certainty. The commissioner
awards, rejects, or picks a manual winner on the Desk; nothing publishes
without that decision.
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.draft_stories import NOISY_POSITIONS


def _skill(picks: list[dict]) -> list[dict]:
    return [p for p in picks if (p.get("position") or "").upper() not in NOISY_POSITIONS]


def _delta_line(p: dict, src: str) -> str:
    return (f"{p['name']} ({p['position']}) — pick #{p['pick_no']}, "
            f"reference rank {p['adp']:g}, delta {p['delta']:+g} ({src})")


def draft_award_nominations(analysis: dict) -> list[dict]:
    if not analysis or not analysis.get("picks"):
        return []
    src = analysis.get("adp_provenance") or "no reference source"
    teams = analysis.get("teams", [])
    picks = analysis.get("picks", [])
    have_deltas = any(p["delta"] is not None for p in picks)
    awards: list[dict] = []

    # --- Draft Crusher (objective proxy: net delta captured, skill positions) ---
    if have_deltas:
        net = []
        for t in teams:
            skill = [p for p in _skill(picks) if p["team_slug"] == t["team_slug"] and p["delta"] is not None]
            if skill:
                net.append((sum(p["delta"] for p in skill), t, skill))
        net.sort(key=lambda x: -x[0])
        awards.append({
            "award_key": "draft-crusher",
            "award_name": "Draft Crusher",
            "kind": "objective-proxy",
            "metric": f"Net picks-vs-reference across skill-position selections ({src}). "
                      "A proxy for value captured, not a grade.",
            "nominees": [
                {
                    "team_slug": t["team_slug"],
                    "metric_value": round(total, 1),
                    "facts": [f"Net delta {total:+.1f} over {len(skill)} skill picks.",
                              "Best three: " + "; ".join(
                                  _delta_line(p, src) for p in sorted(skill, key=lambda p: -p["delta"])[:3])],
                    "evidence": t["evidence"],
                }
                for total, t, skill in net[:4]
            ],
        })

        # --- Best Value (objective: largest positive delta, skill) ---
        values = sorted((p for p in _skill(picks) if p["delta"] is not None and p["delta"] > 0),
                        key=lambda p: -p["delta"])
        awards.append({
            "award_key": "best-value",
            "award_name": "Best Value",
            "kind": "objective",
            "metric": f"Largest positive picks-vs-reference delta ({src}).",
            "nominees": [
                {"team_slug": p["team_slug"], "player": p["name"], "metric_value": p["delta"],
                 "facts": [_delta_line(p, src)], "evidence": p["evidence"]}
                for p in values[:5]
            ],
        })

        # --- Biggest Reach (objective: largest negative delta, skill) ---
        reaches = sorted((p for p in _skill(picks) if p["delta"] is not None and p["delta"] < 0),
                         key=lambda p: p["delta"])
        awards.append({
            "award_key": "biggest-reach",
            "award_name": "Biggest Reach",
            "kind": "objective",
            "metric": f"Largest negative picks-vs-reference delta, K/DST excluded ({src}). "
                      "A reach is a fact about draft position, not a verdict on the pick.",
            "nominees": [
                {"team_slug": p["team_slug"], "player": p["name"], "metric_value": p["delta"],
                 "facts": [_delta_line(p, src)], "evidence": p["evidence"]}
                for p in reaches[:5]
            ],
        })

    # --- Most Aggressive Construction (objective: early single-position load) ---
    aggressive = []
    for t in teams:
        early = t.get("early_rounds_positions", {})
        if early:
            pos, n = max(early.items(), key=lambda kv: kv[1])
            aggressive.append((n, pos, t))
    aggressive.sort(key=lambda x: -x[0])
    awards.append({
        "award_key": "most-aggressive-construction",
        "award_name": "Most Aggressive Construction",
        "kind": "objective",
        "metric": "Most rounds 1-3 picks spent on a single position.",
        "nominees": [
            {"team_slug": t["team_slug"], "metric_value": n,
             "facts": [f"{n} of first-3-round picks on {pos}.",
                       f"Rounds 1-3 mix: {t.get('early_rounds_positions')}"],
             "evidence": t["evidence"] + [evidence.computed_ref(
                 "early-position-concentration", analysis["league"], analysis["season"], t["team_slug"])]}
            for n, pos, t in aggressive[:4] if n >= 2
        ],
    })

    # --- Most Interesting Strategy (subjective; nominees from factual anomalies) ---
    interesting = []
    for t in teams:
        hooks = [a["fact"] for a in t.get("anomalies", [])]
        hooks += [f"Drafted {s['nfl_team']} stack: {s['qb']} + {', '.join(s['partners'])}"
                  for s in t.get("stacks", [])]
        if hooks:
            interesting.append((len(hooks), t, hooks))
    interesting.sort(key=lambda x: -x[0])
    awards.append({
        "award_key": "most-interesting-strategy",
        "award_name": "Most Interesting Strategy",
        "kind": "subjective",
        "metric": "No objective metric — nominees have the most factual deviations "
                  "from league norms; interest is the commissioner's call.",
        "nominees": [
            {"team_slug": t["team_slug"], "facts": hooks, "evidence": t["evidence"]}
            for _, t, hooks in interesting[:4]
        ],
    })

    # --- Most Likely to Age Badly (subjective; heuristic candidate list) ---
    if have_deltas:
        risky = sorted((p for p in _skill(picks) if p["delta"] is not None and p["delta"] <= -15),
                       key=lambda p: p["delta"])
        awards.append({
            "award_key": "most-likely-to-age-badly",
            "award_name": "Most Likely to Age Badly",
            "kind": "subjective",
            "metric": "No objective metric — candidates are simply the largest "
                      "skill-position deviations from reference rank. Aging badly is a "
                      "prediction only the commissioner may make (and it becomes a Take).",
            "nominees": [
                {"team_slug": p["team_slug"], "player": p["name"],
                 "facts": [_delta_line(p, src)], "evidence": p["evidence"]}
                for p in risky[:5]
            ],
        })

    return [a for a in awards if a["nominees"]]
