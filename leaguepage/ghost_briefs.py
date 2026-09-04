"""Ghost writing briefs for the Issue Editor.

The authoring model (Jonathan, 2026-08-30): empty sections start as a
compact, private writing brief rendered as ghost text inside the editor,
never as prewritten prose. The brief is AMMUNITION: strongest facts,
possible angles, callbacks, and a suggested structure. Commissioner text is
the only publishable layer; briefs live only on the private Desk, are
computed live from synced data at page load, and are never stored in issue
content, snapshots, or dist/.

Relevance filter per section: ~3-7 strongest facts, 2-4 angles, 0-2
callbacks, one counterpoint. Deep data stays behind the evidence drawer.
"""
from __future__ import annotations

from leaguepage.adp import load_adp_for_league
from leaguepage.config import League
from leaguepage.storage import Storage

STRUCTURES = {
    "lowdown": [
        "1. State the premise in the first sentence.",
        "2. Roast your own call first (buys the license for the rest).",
        "3. Two or three league examples, each with its number.",
        "4. Turn: what the season will actually test.",
        "5. Generous close, then puncture it.",
    ],
    "draft-capsules": [
        "Per team: verdict first, one number, one construction note,",
        "one joke. Your own capsule goes first and hardest.",
    ],
    "hardware": [
        "Per award: winner, the number that decided it, one runner-up",
        "mention where the race was close.",
    ],
}


def _fmt_delta(p: dict) -> str:
    return f"{p['name']} pick {p['pick_no']} vs rank {p['adp']:g} ({p['delta']:+g})"


def _draft_data(storage: Storage, league: League):
    from leaguepage.draft_analysis import analyze_league_draft
    from leaguepage.editorial import load_coalitions, load_managers
    from leaguepage.team_names import resolve_public_names

    managers = load_managers()
    analysis = analyze_league_draft(storage, league, managers=managers,
                                    adp=load_adp_for_league(league))
    resolved = resolve_public_names(storage, league)
    names = {rid: (v["name"] or f"Roster {rid}") for rid, v in resolved.items()}
    return analysis, managers, load_coalitions(), names


def _team_net(t: dict) -> float:
    return sum(p["delta"] for p in t["picks_by_round"] if p.get("delta") is not None)


def _candidate_lines(storage: Storage, league: League, season: str, issue_key: str,
                     analysis, managers, coalitions, names: dict[int, str],
                     limit: int = 7) -> list[str]:
    from leaguepage.draft_stories import draft_story_candidates

    slug_to_name = {t["team_slug"]: names.get(t["roster_id"], t["team_slug"])
                    for t in analysis["teams"]}
    decisions = storage.get_story_decisions(league.slug, season, issue_key)
    out = []
    for c in draft_story_candidates(analysis, storage=storage, managers=managers,
                                    coalitions=coalitions)[:limit]:
        d = decisions.get(c["candidate_id"])
        mark = f"  [{d['decision'].upper()}]" if d else ""
        fact = f" ({c['facts'][0]})" if c.get("facts") else ""
        line = f"• {c['headline']}{fact}{mark}"
        for slug, nm in slug_to_name.items():  # candidates speak in slugs
            line = line.replace(slug, nm)
        out.append(line)
    return out


def brief_for_section(storage: Storage, league: League, season: str,
                      issue_key: str, section: str, week: int | None) -> dict:
    """{'text': ghost text, 'evidence': [...], 'data_as_of': iso|None}.
    Never raises: a failed part is skipped, an empty brief returns text ''."""
    try:
        return _build(storage, league, season, issue_key, section, week)
    except Exception as exc:
        return {"text": f"(writing brief unavailable: {type(exc).__name__})",
                "evidence": [], "data_as_of": None}


def _build(storage, league, season, issue_key, section, week) -> dict:
    data_as_of = None
    fr = storage._conn.execute(  # noqa: SLF001 - read-only freshness stamp
        "SELECT fetched_at FROM leagues WHERE league_id=?", (league.league_id,)).fetchone()
    if fr:
        data_as_of = fr["fetched_at"]

    if section.startswith("matchup:"):
        return {**_matchup_brief(storage, league, season, week, section.removeprefix("matchup:")),
                "data_as_of": data_as_of}

    analysis, managers, coalitions, names = _draft_data(storage, league)
    lines: list[str] = []
    evidence: list[str] = []
    if not analysis:
        return {"text": "(no draft data synced yet)", "evidence": [], "data_as_of": data_as_of}

    nets = sorted(((t, _team_net(t)) for t in analysis["teams"]), key=lambda x: -x[1])
    top_reach = analysis["league_biggest_reaches"][:2]
    top_value = analysis["league_biggest_values"][:2]

    if section == "lowdown":
        # Once games have been played, the draft is context rather than the
        # story. This branch listed reaches and steals as the STRONGEST
        # NUMBERS in week 9, which is a brief for an issue that came out in
        # August.
        played = _weeks_played(storage, league)
        lines.append("WORTH MENTIONING")
        lines += _candidate_lines(storage, league, season, issue_key,
                                  analysis, managers, coalitions, names)
        lines.append("")
        lines.append("STRONGEST NUMBERS" if not played
                     else f"THE DRAFT, {played} WEEKS ON (context, not the story)")
        for p in top_reach:
            lines.append(f"• Boldest pick: {_fmt_delta(p)} ({names.get(_rid_of(analysis, p['team_slug']))})")
        for p in top_value:
            lines.append(f"• Best value: {_fmt_delta(p)} ({names.get(_rid_of(analysis, p['team_slug']))})")
        for p in top_reach[:1] + top_value[:1]:
            used_in = _published_mentions(league, p.get("name"))
            if used_in >= 2:
                lines.append(f"  (repetition check: {p['name']} already "
                             f"appears in {used_in} published issues — vary "
                             "the angle before calling it back again)")
        stacks = sum(len(t["stacks"]) for t in analysis["teams"])
        lines.append(f"• {analysis['pick_count']} picks, {stacks} QB stacks league-wide")
        try:
            from leaguepage.team_analytics import league_shift_lines

            shifts = league_shift_lines(storage, league, season,
                                        _weeks_played(storage, league), names)
            if shifts:
                lines.append("")
                lines.append("SHIFTS WORTH A LINE (vs last snapshot)")
                lines += shifts
        except Exception:
            pass
        takes = storage.all_takes(league.slug, season)
        if takes:
            lines.append("")
            lines.append("YOUR OWN PRIOR CALLS (future False Assumptions material)")
            for t in takes[:2]:
                lines.append(f"• \"{t['quote']}\" ({t['context']})")
        if played:
            # What the season has actually produced, which is what a week-9
            # Lowdown is about.
            from leaguepage.team_analytics import playoff_outlook, recent_form

            lines.append("")
            lines.append(f"THE SEASON SO FAR ({played} weeks played)")
            form = recent_form(storage, league, played) or {}
            hot = sorted(form.items(), key=lambda kv: kv[1]["rank"])[:2]
            for rid, f in hot:
                lines.append(f"• {names.get(rid)}: #{f['rank']} scoring over "
                             f"the last {f['window_label']}")
            outlook = playoff_outlook(storage, league, played)
            if outlook.get("teams"):
                ranked = sorted(outlook["teams"].items(),
                                key=lambda kv: -kv[1]["odds"])
                lines.append(f"• {outlook['note']}")
                for rid, t in ranked[:2] + ranked[-2:]:
                    lines.append(f"  - {names.get(rid)}: {t['band']}")
        lines.append("")
        lines.append("POSSIBLE STRUCTURE")
        lines += STRUCTURES["lowdown"]
        evidence = [f"{_fmt_delta(p)}" for p in
                    analysis["league_biggest_reaches"][:6] + analysis["league_biggest_values"][:6]]

    elif section == "draft-capsules":
        from leaguepage.draft_value import consensus_style, team_draft_profile

        profs = {t["roster_id"]: team_draft_profile(t, analysis["total_teams"])
                 for t in analysis["teams"]}
        styles = consensus_style(profs)
        lines.append("ONE LINE PER TEAM (net vs consensus; best; worst; shape)")
        for t, net in nets:
            nm = names.get(t["roster_id"], t["team_slug"])
            bv, br = t.get("biggest_value"), t.get("biggest_reach")
            bits = [f"net {net:+g}"]
            pr = profs[t["roster_id"]]
            if pr["reach_picks"] or pr["steal_picks"]:
                bits.append(f"{pr['reach_picks']}x >=1rd early, "
                            f"{pr['steal_picks']}x >=1rd late")
            if styles.get(t["roster_id"]):
                bits.append(styles[t["roster_id"]])
            if bv:
                bits.append(f"best {bv['name']} {bv['delta']:+g}")
            if br:
                bits.append(f"worst {br['name']} {br['delta']:+g}")
            conc = t["nfl_team_concentration"]
            if conc:
                bits.append(f"{conc[0]['count']}x {conc[0]['nfl_team']}")
            pos = t["position_counts"]
            bits.append("/".join(f"{n}{p}" for p, n in sorted(pos.items())))
            lines.append(f"• {nm}: " + ", ".join(bits))
        lines.append("")
        lines.append("POSSIBLE FRAMES")
        lines.append("• Grade the decision, never the person; own board gets it worst.")
        lines.append("• One construction identity per team beats a stat dump.")
        lines += ["", "STRUCTURE"] + STRUCTURES["draft-capsules"]
        evidence = [e for t in analysis["teams"] for e in (t.get("evidence") or [])[:2]]

    elif section == "hardware":
        from leaguepage.draft_awards import draft_award_nominations

        decisions = storage.get_award_decisions(league.slug, season, issue_key)
        name_of = {t["team_slug"]: names.get(t["roster_id"], t["team_slug"])
                   for t in analysis["teams"]}
        lines.append("AWARD RACES (system nominations; decisions marked)")
        for a in draft_award_nominations(analysis):
            if not a.get("nominees"):
                continue
            d = decisions.get(a["award_key"])
            lead = a["nominees"][0]
            fact = (lead.get("facts") or [""])[0]
            mark = f"  [{d['decision'].upper()}: {d.get('winner') or ''}]" if d else ""
            lines.append(f"• {a['award_name']}: {name_of.get(lead['team_slug'], lead['team_slug'])}"
                         f" ({fact}){mark}")
            for n in a["nominees"][1:3]:
                evidence.append(f"{a['award_name']} runner-up "
                                f"{name_of.get(n['team_slug'], n['team_slug'])}: "
                                f"{(n.get('facts') or [''])[0]}")
        lines += ["", "STRUCTURE"] + STRUCTURES["hardware"]

    elif section == "power":
        current = {p["roster_id"]: p for p in storage.get_power_rankings(
            league.slug, season, "preseason" if week is None else issue_key)}
        lines.append("SYSTEM ORDER (net draft value vs consensus; your call is the ranking)")
        for i, (t, net) in enumerate(nets, 1):
            rid = t["roster_id"]
            cur = current.get(rid)
            saved = f"  [saved rank {cur['rank']}]" if cur and cur.get("rank") else ""
            lines.append(f"• {i}. {names.get(rid)} (net {net:+g}){saved}")
        lines.append("• Counterpoint: consensus value is one input; roster ceilings and")
        lines.append("  schedule are yours to weigh. The ranking is a commissioner call.")

    elif section == "forceflow":
        lines.append("MEANINGFUL MOVES (rationale engine; inferred, never stated intent)")
        moves = _move_lines(storage, league, limit=5)
        lines += moves or ["• (no meaningful transactions yet)"]
        lines.append("")
        lines.append("STRONGEST NUMBERS")
        for p in top_reach[:1] + top_value[:1]:
            lines.append(f"• {_fmt_delta(p)}")

    else:  # custom / tracks / fades / blackbox / branches ...
        decisions = storage.get_story_decisions(league.slug, season, issue_key)
        routed = _candidate_lines(storage, league, season, issue_key,
                                  analysis, managers, coalitions, names, limit=10)
        lines.append("MATERIAL ROUTED OR ROUTABLE HERE")
        lines += routed[:5] or ["• (no story candidates surfaced for this section yet)"]
        lines.append("")
        lines.append("STRONGEST NUMBERS")
        for p in top_reach[:1] + top_value[:1]:
            lines.append(f"• {_fmt_delta(p)}")

    return {"text": "\n".join(lines).strip(), "evidence": evidence[:12],
            "data_as_of": data_as_of}


def _rid_of(analysis: dict, team_slug: str) -> int | None:
    for t in analysis["teams"]:
        if t["team_slug"] == team_slug:
            return t["roster_id"]
    return None


def _matchup_brief(storage, league, season, week, slug) -> dict:
    """Everything he was looking up by hand, per team, before writing.

    The old brief opened with why the system found this matchup
    interesting, which is a question that was already answered by the time
    he clicked into the card. It now opens with who plays, who might not,
    and what each side has to get past, because that is what a preview is
    made of. Scoring and angles come after.

    Every line is PRIVATE research. None of it publishes; it reaches a
    reader only where he writes it into his own prose.
    """
    from leaguepage import matchup_research as research
    from leaguepage.matchup_packet import compute_week
    from leaguepage.team_analytics import player_values, positional_profile
    from leaguepage.team_names import resolve_public_names

    computed = compute_week(storage, league, week)
    sm_entry = next((s for s in (computed or {}).get("scored", [])
                     if s["matchup"]["matchup_slug"] == slug), None)
    if not sm_entry:
        return {"text": "(no matchup data for this week yet)", "evidence": []}
    resolved = resolve_public_names(storage, league)
    m = sm_entry["matchup"]
    weeks_played = (computed or {}).get("analysis", {}).get("weeks_played", 0)
    lines, evidence = [], []

    def name_of(t):
        return (resolved.get(t["roster_id"]) or {}).get("name") or f"Roster {t['roster_id']}"

    a, b = m["teams"]
    na, nb = name_of(a), name_of(b)

    try:
        values, stage = player_values(storage, league, weeks_played=weeks_played)
        profile = positional_profile(storage, league, weeks_played=weeks_played)
    except Exception:
        values, stage, profile = {}, "unavailable", {}

    def block(heading, per_team):
        lines.append(heading)
        for t, nm in ((a, na), (b, nb)):
            body = per_team(t, nm)
            if not body:
                continue
            lines.append(f"{nm}:")
            lines.extend(body)          # `+=` would rebind `lines` in here
        lines.append("")

    block("WHO DECIDES IT",
          lambda t, nm: research.key_players(storage, league, t, values, stage))
    block("WHO MIGHT NOT PLAY",
          lambda t, nm: research.availability(storage, league, t))
    lines.insert(len(lines) - 1, research.bye_note())
    block("WHAT EACH SIDE HAS TO GET PAST",
          lambda t, nm: research.gap_to_close(
              profile, t, b if t is a else a, nm, nb if t is a else na,
              weeks_played=weeks_played) if profile else [])
    block("WHAT THEY JUST DID (rationale is inferred, never their stated motive)",
          lambda t, nm: research.recent_moves(storage, league, t["roster_id"],
                                              weeks_played))
    block("WHAT THEY COULD STILL DO",
          lambda t, nm: research.possible_moves(storage, league, profile, t,
                                                weeks_played=weeks_played)
          if profile else [])
    block("ON THE RECORD AGAINST THEM (private; roast ammunition, not a joke)",
          lambda t, nm: research.self_inflicted(storage, league, t,
                                                weeks_played=weeks_played))

    reason, cb_lines = research.notable_callback(m, resolved)
    if reason:
        lines.append("THESE TWO, LAST TIME")
        lines.extend(cb_lines)
        lines.append("")

    lines.append("WHY THIS ONE MATTERS")
    comps = (sm_entry["competitive_importance"]["components"]
             + sm_entry["story_value"]["components"])
    for c in comps[:4]:
        lines.append(f"  {c['label']} (+{c['points']})")
        evidence += c.get("evidence", [])[:2]
    if sm_entry.get("tags"):
        lines.append(f"  tags: {', '.join(sm_entry['tags'])}")

    angles = sm_entry.get("angles") or []
    if angles:
        lines.append("")
        lines.append("POSSIBLE ANGLES (pick one; collisions flagged)")
        for ang in angles[:4]:
            warn = "  [recently used nearby]" if ang.get("collision_warnings") else ""
            lines.append(f"  [{ang.get('family', '?')}] "
                         f"{ang.get('title', ang.get('angle_id', '?'))}: "
                         f"{_clip(ang.get('premise', ''), 100)}{warn}")

    sm = sm_entry.get("story_memory") or {}
    cbs = (sm.get("callbacks") or [])[:2]
    if cbs:
        lines.append("")
        lines.append("ARCHIVE CALLBACKS (same league only)")
        for cb in cbs:
            excerpt = _clip(cb.get("excerpt") or cb.get("why") or "", 100)
            lines.append(f"  {cb.get('title', '?')}"
                         + (f": {excerpt}" if excerpt else ""))
    used = storage.recent_editorial_usage(league.slug, season, kind="joke_family")
    if used:
        lines.append(f"  avoid repeating: {', '.join(sorted({u['value'] for u in used[:5]}))}")

    return {"text": "\n".join(lines).strip(), "evidence": evidence[:12]}


def _clip(text: str, limit: int) -> str:
    """Word-boundary truncation — a brief line cut mid-word reads broken."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …"


def _published_mentions(league: League, name: str | None) -> int:
    """How many PUBLISHED issues already mention a player — repetition
    flag only, never suppression: factual callbacks stay allowed."""
    if not name:
        return 0
    from leaguepage.config import PUBLISHED_DIR

    root = PUBLISHED_DIR / league.slug
    if not root.exists():
        return 0
    count = 0
    for f in root.rglob("*.json"):
        try:
            if name in f.read_text(encoding="utf-8"):
                count += 1
        except OSError:
            pass
    return count


def _weeks_played(storage: Storage, league: League) -> int:
    from leaguepage.matchup_analysis import weekly_scores

    scores = weekly_scores(storage, league.league_id, 18)
    return max((len(v) for v in scores.values()), default=0)


def _move_lines(storage: Storage, league: League, *, rids=None,
                limit: int = 4) -> list[str]:
    """Private ammunition from the transaction rationale engine: facts and
    the inferred reading, never a written paragraph."""
    from leaguepage.transaction_analysis import analyze_transactions

    lines: list[str] = []
    shown = 0
    for row in analyze_transactions(storage, league,
                                    _weeks_played(storage, league)):
        if not row.get("significant"):
            continue
        if rids is not None and not (set(row["rids"]) & set(rids)):
            continue
        from leaguepage.transaction_analysis import describe_move

        bits = [f"wk {row['week']}"]
        if row.get("faab"):
            bits.append(f"{round(row['faab_share'] * 100)}% of budget")
        lines.append(f"• {describe_move(row)} ({', '.join(bits)})")
        if row["rationale"].get("text"):
            lines.append(f"  {row['rationale']['text']}")
        if row.get("rank_shift"):
            lines.append(f"  Positional shift: {row['rank_shift']}")
        if row.get("outcome"):
            lines.append(f"  {row['outcome']}")
        shown += 1
        if shown >= limit:
            break
    return lines
