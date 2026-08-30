"""Weekly editorial signals: Story Board candidates, Tracks of Interest,
Fades, Force Flow, and Black Box detection.

All deterministic, all evidence-backed, all honest about sample size. Nothing
here decides what publishes — candidates carry facts and 'why surfaced';
the commissioner selects; Claude Code writes prose for selections only.
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.config import League
from leaguepage.editorial import confirmed_coalition_mappings
from leaguepage.matchup_analysis import weekly_scores
from leaguepage.storage import Storage

TREND_WINDOW = 3          # weeks for Tracks/Fades trends
MIN_BLACKBOX_WEEKS = 3    # a "record" over fewer weeks is trivia, not a record
FORCE_FLOW_FAAB_PCT = 0.15
NOTABLE_DROP_SEARCH_RANK = 120


def _cand(cid, category, headline, *, teams=None, players=None, facts=None,
          ev=None, why="", sections=None, confidence=None):
    return {
        "candidate_id": cid, "category": category, "headline": headline,
        "teams": teams or [], "players": players or [],
        "facts": facts or [], "evidence": ev or [], "why": why,
        "recommended_sections": sections or [],
        "confidence": confidence,
    }


# ---------------------------------------------------------------- Force Flow

def force_flow_candidates(storage: Storage, league: League, week: int,
                          team_slugs: dict[int, str]) -> list[dict]:
    """Transactions with editorial interest only — never the raw feed."""
    league_data = storage.get_league(league.league_id) or {}
    budget = (league_data.get("settings") or {}).get("waiver_budget") or 100
    week_rows = {r["roster_id"]: r for r in storage.get_matchups(league.league_id, week)}
    out = []
    for w in range(max(1, week - 1), week + 1):
        for t in storage.get_transactions(league.league_id, w):
            if t.get("status") != "complete":
                continue
            tid = t.get("transaction_id")
            ttype = t.get("type")
            faab = sum(x.get("amount", 0) for x in (t.get("waiver_budget") or []))
            reasons, facts, players, teams = [], [], [], []
            for pid, rid in (t.get("adds") or {}).items():
                p = storage.get_player(pid) or {}
                players.append(p.get("full_name") or pid)
                teams.append(team_slugs.get(rid, f"roster-{rid}"))
                raw = week_rows.get(rid) or {}
                if pid in (raw.get("starters") or []):
                    reasons.append("added player started immediately")
            for pid, rid in (t.get("drops") or {}).items():
                p = storage.get_player(pid) or {}
                rank = p.get("search_rank") or 10**6
                if rank <= NOTABLE_DROP_SEARCH_RANK:
                    reasons.append(f"dropped a widely-rostered player "
                                   f"({p.get('full_name')}, Sleeper search rank {rank} — "
                                   "a popularity proxy, labeled as such)")
                    players.append(p.get("full_name") or pid)
                    teams.append(team_slugs.get(rid, f"roster-{rid}"))
            if ttype == "trade":
                reasons.append("trade")
            if faab >= FORCE_FLOW_FAAB_PCT * budget:
                reasons.append(f"large FAAB spend ({faab}/{budget})")
            if not reasons:
                continue
            facts.append(f"Week {w} {ttype}: adds {list((t.get('adds') or {}).keys()) or 'none'}, "
                         f"drops {list((t.get('drops') or {}).keys()) or 'none'}"
                         + (f", FAAB {faab}" if faab else "") + ".")
            out.append(_cand(
                f"forceflow:{tid}", "force-flow",
                f"{ttype.replace('_', ' ').title()}: {', '.join(sorted(set(players))[:3]) or 'roster move'}",
                teams=sorted(set(teams)), players=sorted(set(players)),
                facts=facts, ev=[f"sleeper:transaction:{tid}"],
                why="; ".join(sorted(set(reasons))),
                sections=["force-flow"],
            ))
    return out


# ------------------------------------------------------------ Tracks / Fades

def tracks_and_fades(storage: Storage, league: League, week: int,
                     analysis: dict) -> tuple[list[dict], list[dict]]:
    """Trend nominations over the last TREND_WINDOW played weeks. Empty when
    fewer than 2 weeks have been played — a one-week trend is a result."""
    weeks_played = (analysis or {}).get("weeks_played", 0)
    if weeks_played < 2:
        return [], []
    limitation = (f"Basis: last {min(TREND_WINDOW, weeks_played)} of {weeks_played} "
                  "played weeks; small samples, stated as such.")
    tracks, fades = [], []  # entries carry a _priority for ranking before the cap
    for slug, t in analysis["teams"].items():
        ap = t.get("all_play") or {}
        rec = t["record"]
        games = rec["wins"] + rec["losses"]
        win_pct = rec["wins"] / games if games else 0
        ap_pct = ap.get("pct")
        recent = t.get("recent_scores") or []
        ev = [evidence.roster_ref(league.league_id, t["roster_id"]),
              evidence.computed_ref("trend", league.slug, analysis["season"], slug)]
        hooks_up, hooks_down = [], []
        if ap_pct is not None and games:
            if ap_pct - win_pct >= 0.2:
                hooks_up.append(f"all-play {ap['wins']}-{ap['losses']} ({ap_pct:.0%}) runs "
                                f"well ahead of the {rec['wins']}-{rec['losses']} record")
            if win_pct - ap_pct >= 0.2:
                hooks_down.append(f"record {rec['wins']}-{rec['losses']} outruns all-play "
                                  f"{ap['wins']}-{ap['losses']} ({ap_pct:.0%})")
        if len(recent) >= 2 and recent[-1] > recent[0] * 1.15:
            hooks_up.append(f"scoring rising across the window ({recent[0]:g} → {recent[-1]:g})")
        if len(recent) >= 2 and recent[-1] < recent[0] * 0.85:
            hooks_down.append(f"scoring falling across the window ({recent[0]:g} → {recent[-1]:g})")
        streak = t.get("streak")
        if streak and streak.startswith("W") and int(streak[1:]) >= 3:
            hooks_up.append(f"{streak} streak")
        if streak and streak.startswith("L") and int(streak[1:]) >= 3:
            hooks_down.append(f"{streak} streak")
        def _priority(hooks: list[str]) -> int:
            # the record-vs-all-play divergence is the strongest signal;
            # streaks alone are the weakest
            score = 0
            for h in hooks:
                if "all-play" in h:
                    score += 2
                elif "scoring" in h:
                    score += 1
                else:
                    score += 1
            return score + (1 if any("all-play" in h for h in hooks) else 0)

        if hooks_up and t["standing"] > 2:  # don't just duplicate the top of the table
            c = _cand(
                f"track:{slug}", "track", f"Track of Interest: {slug}",
                teams=[slug], facts=["; ".join(hooks_up) + ".", limitation],
                ev=ev, why="Underlying trend runs ahead of surface results.",
                sections=["tracks"], confidence="trend-window",
            )
            c["_priority"] = _priority(hooks_up)
            tracks.append(c)
        if hooks_down:
            c = _cand(
                f"fade:{slug}", "fade", f"Fade: {slug}",
                teams=[slug], facts=["; ".join(hooks_down) + ".", limitation],
                ev=ev, why="Underlying trend runs behind surface results. The system "
                           "states the evidence; only editorial prose calls it a fade.",
                sections=["fades"], confidence="trend-window",
            )
            c["_priority"] = _priority(hooks_down)
            fades.append(c)
    tracks.sort(key=lambda c: -c.pop("_priority"))
    fades.sort(key=lambda c: -c.pop("_priority"))
    both = {c["teams"][0] for c in tracks} & {c["teams"][0] for c in fades}
    for c in tracks + fades:
        if c["teams"][0] in both:
            c["facts"].append("WARNING: this team is nominated in both Tracks and Fades; "
                              "pick at most one unless the split story is deliberate.")
    return tracks[:4], fades[:3]


# ---------------------------------------------------------------- Black Box

def black_box_events(storage: Storage, league: League, week: int, analysis: dict) -> list[dict]:
    """Statistical anomalies against this season's stored population. Nothing
    interesting → empty list → the public section disappears entirely."""
    scores = weekly_scores(storage, league.league_id, week)
    all_rows = [(rid, wk, pts) for rid, rows in scores.items() for wk, pts in rows]
    weeks_seen = sorted({wk for _, wk, _ in all_rows})
    if len(weeks_seen) < MIN_BLACKBOX_WEEKS:
        return []
    population = f"weeks {weeks_seen[0]}-{weeks_seen[-1]} of {analysis['season']} ({league.slug})"
    slug_of = {t["roster_id"]: s for s, t in analysis["teams"].items()}
    this_week = [(rid, pts) for rid, wk, pts in all_rows if wk == week]
    if not this_week:
        return []
    events = []
    season_high = max(pts for _, _, pts in all_rows)
    season_low = min(pts for _, _, pts in all_rows)
    for rid, pts in this_week:
        ev = [evidence.roster_ref(league.league_id, rid),
              evidence.computed_ref("blackbox", league.slug, analysis["season"], str(week))]
        if pts >= season_high:
            events.append(_cand(
                f"blackbox:season-high:{week}:{rid}", "black-box",
                f"Season-high score: {slug_of.get(rid)} put up {pts:g}",
                teams=[slug_of.get(rid)], facts=[f"Highest single-week score of {population}."],
                ev=ev, why="New season high.", sections=["black-box"], confidence=population,
            ))
        if pts <= season_low:
            events.append(_cand(
                f"blackbox:season-low:{week}:{rid}", "black-box",
                f"Season-low score: {slug_of.get(rid)} managed {pts:g}",
                teams=[slug_of.get(rid)], facts=[f"Lowest single-week score of {population}."],
                ev=ev, why="New season low.", sections=["black-box"], confidence=population,
            ))
    # margins this week vs season
    margins = []
    for m in analysis["matchups"]:
        a, b = m["teams"]
        if a["points"] is not None and b["points"] is not None:
            margins.append((abs(a["points"] - b["points"]), m))
    if margins:
        season_margins = []
        for wk in weeks_seen:
            rows = storage.get_matchups(league.league_id, wk)
            by_mid = {}
            for r in rows:
                if r.get("matchup_id") is not None:
                    by_mid.setdefault(r["matchup_id"], []).append(float(r.get("points") or 0))
            season_margins += [abs(p[0] - p[1]) for p in by_mid.values() if len(p) == 2]
        big, m = max(margins, key=lambda x: x[0])
        small, m2 = min(margins, key=lambda x: x[0])
        if season_margins and big >= max(season_margins):
            events.append(_cand(
                f"blackbox:margin-high:{week}", "black-box",
                f"Largest margin of the season: {big:g}",
                teams=[t["team_slug"] for t in m["teams"]],
                facts=[f"Population: {population}."], ev=m["evidence"],
                why="Blowout record.", sections=["black-box"], confidence=population,
            ))
        if season_margins and small <= min(season_margins):
            events.append(_cand(
                f"blackbox:margin-low:{week}", "black-box",
                f"Narrowest margin of the season: {small:g}",
                teams=[t["team_slug"] for t in m2["teams"]],
                facts=[f"Population: {population}."], ev=m2["evidence"],
                why="Photo-finish record.", sections=["black-box"], confidence=population,
            ))
    for slug, t in analysis["teams"].items():
        streak = t.get("streak")
        if streak and int(streak[1:]) >= 4:
            events.append(_cand(
                f"blackbox:streak:{week}:{slug}", "black-box",
                f"{slug} is on a {streak} streak",
                teams=[slug], facts=[f"Population: {population}."],
                ev=[evidence.roster_ref(league.league_id, t["roster_id"])],
                why="Streak of 4+.", sections=["black-box"], confidence=population,
            ))
    return events


# ---------------------------------------------------------- Weekly Story Board

def weekly_story_candidates(
    storage: Storage,
    league: League,
    week: int,
    computed_week: dict,
    *,
    coalitions: dict | None = None,
) -> list[dict]:
    """The full weekly Story Board: matchup-derived + results + transactions +
    trends + records + takes + coalition storylines."""
    analysis = computed_week["analysis"]
    season = analysis["season"]
    team_slugs = {t["roster_id"]: s for s, t in analysis["teams"].items()}
    candidates: list[dict] = []

    # matchup interest tops (link into Matchup Lab rather than duplicating it)
    ranked = sorted(computed_week["scored"],
                    key=lambda s: -(s["competitive_importance"]["score"] + s["story_value"]["score"]))
    for s in ranked[:3]:
        m = s["matchup"]
        candidates.append(_cand(
            f"story:matchup:{m['matchup_slug']}", "matchup",
            f"Matchup: {m['matchup_slug']} "
            f"(CI {s['competitive_importance']['score']}, SV {s['story_value']['score']})",
            teams=[t["team_slug"] for t in m["teams"]],
            facts=[c["label"] for c in s["competitive_importance"]["components"][:3]],
            ev=m["evidence"],
            why="Top combined editorial interest this week (heuristic, components visible).",
            sections=["ctp", "lowdown"],
        ))

    # played-week results
    played = [m for m in analysis["matchups"]
              if all(t["points"] is not None for t in m["teams"])]
    if played:
        def margin(m):
            return abs(m["teams"][0]["points"] - m["teams"][1]["points"])
        big = max(played, key=margin)
        close = min(played, key=margin)
        high = max((t for m in played for t in m["teams"]), key=lambda t: t["points"])
        low = min((t for m in played for t in m["teams"]), key=lambda t: t["points"])
        candidates += [
            _cand(f"story:blowout:{week}", "result",
                  f"Blowout: {big['matchup_slug']} decided by {margin(big):g}",
                  teams=[t["team_slug"] for t in big["teams"]], ev=big["evidence"],
                  why="Largest margin of the week.", sections=["lowdown", "ctp"]),
            _cand(f"story:photo-finish:{week}", "result",
                  f"Photo finish: {close['matchup_slug']} decided by {margin(close):g}",
                  teams=[t["team_slug"] for t in close["teams"]], ev=close["evidence"],
                  why="Narrowest margin of the week.", sections=["lowdown", "ctp"]),
            _cand(f"story:high-score:{week}", "result",
                  f"Week-high score: {high['team_slug']} with {high['points']:g}",
                  teams=[high["team_slug"]],
                  ev=[evidence.roster_ref(league.league_id, high["roster_id"])],
                  why="Highest score of the week.", sections=["lowdown"]),
            _cand(f"story:low-score:{week}", "result",
                  f"Week-low score: {low['team_slug']} with {low['points']:g}",
                  teams=[low["team_slug"]],
                  ev=[evidence.roster_ref(league.league_id, low["roster_id"])],
                  why="Lowest score of the week.", sections=["lowdown", "awards"]),
        ]

    candidates += force_flow_candidates(storage, league, week, team_slugs)
    tracks, fades = tracks_and_fades(storage, league, week, analysis)
    candidates += tracks + fades
    candidates += black_box_events(storage, league, week, analysis)

    # open takes touching this week's teams
    for t in storage.open_takes(league.slug):
        subj_slugs = set(team_slugs.values())
        if t["subject"] in subj_slugs:
            candidates.append(_cand(
                f"story:take:{t['take_id']}", "take",
                f"Open take on {t['subject']}: \"{t['quote'][:60]}\"",
                teams=[t["subject"]],
                facts=[f"Tracked {t['context'] or '?'} {t['season']}; status {t['status']}."],
                ev=[evidence.take_ref(t["take_id"])],
                why="A tracked take involves a team in action this week.",
                sections=["lowdown", "false-assumptions"] if league.slug == "surfeit" else ["lowdown"],
            ))

    # coalition storyline when a confirmed coalition matchup happens
    if coalitions:
        mapped = {c["roster_mapping"]["roster_id"]: c for c in confirmed_coalition_mappings(coalitions)
                  if c["roster_mapping"].get("league") == league.slug}
        for m in analysis["matchups"]:
            rids = {t["roster_id"] for t in m["teams"]}
            hit = [mapped[r] for r in rids if r in mapped]
            if len(hit) == 2:
                candidates.append(_cand(
                    f"story:coalition-clash:{m['matchup_slug']}", "coalition",
                    f"Coalition Warfare: {hit[0]['name']} vs {hit[1]['name']}",
                    teams=[t["team_slug"] for t in m["teams"]],
                    facts=["Both confirmed coalition teams meet head to head."],
                    ev=[f"editorial:coalition:{c['key']}" for c in hit] + m["evidence"],
                    why="The league's confirmed coalition rivalry is on the schedule.",
                    sections=["ctp", "lowdown"],
                ))
            elif len(hit) == 1:
                candidates.append(_cand(
                    f"story:coalition:{m['matchup_slug']}", "coalition",
                    f"Coalition team in action: {hit[0]['name']}",
                    teams=[t["team_slug"] for t in m["teams"]],
                    facts=[], ev=[f"editorial:coalition:{hit[0]['key']}"] + m["evidence"],
                    why="Confirmed coalition team plays this week.",
                    sections=["ctp"],
                ))

    # analytics layer: playoff swings, movers, streaks, divergence (empty
    # until games are played; deltas need persisted snapshots)
    try:
        from leaguepage.team_analytics import analytics_story_candidates
        from leaguepage.team_names import resolve_public_names

        nm = {rid: (v["name"] or f"Roster {rid}")
              for rid, v in resolve_public_names(storage, league).items()}
        for c in analytics_story_candidates(storage, league, season, week, nm):
            candidates.append(_cand(
                c["candidate_id"], c["category"], c["headline"],
                facts=c["facts"], ev=c["evidence"],
                why="analytics delta vs persisted weekly snapshot",
                sections=c["recommended_sections"]))
    except Exception:
        pass

    return candidates
