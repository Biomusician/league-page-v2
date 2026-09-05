"""Editorial Command Brief — one page, before he writes.

The Desk computes a great deal and has, until now, presented it as boards:
a story board, an awards board, a review packet of decision states. None
of those answers the question an editor asks on Tuesday, which is "what
are this week's three or four stories, and what do I have to back them
up?" This is that page.

Everything here is deterministic and carries its evidence. The ranking of
stories is arithmetic over named signals -- a flagged transaction, an
injured starter on a top room, a roster built lopsided, a matchup with
something beyond the baseline, a take the engine has a reading on -- and
every line says which. Nothing is generated prose, nothing is a
recommendation to publish, and nothing here reaches a reader.

It is written to `COMMAND_BRIEF.md` in the issue directory by the research
build, and rendered on the Desk from there. The Lowdown prep quotes its top
stories, so the ammunition he starts from is the same ranking.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from leaguepage.config import League
from leaguepage.issue_builder import issue_dir
from leaguepage.storage import Storage

TOP_STORIES = 5
MATCHUPS_TO_WATCH = 4
# Matchup interest starts from a baseline both axes hand out for free
# (competitive 25, story 10). Only what sits above it says anything.
INTEREST_BASELINE = 35
# A reference-rank snapshot older than this is stale enough to say so.
REFERENCE_STALE_DAYS = 21
OUT_STATUSES = {"IR", "Out", "Doubtful", "PUP", "Sus", "NA"}
# A kicker or defense room is not a construction story on either side of
# the ledger: the same calibration that keeps K/DST out of the draft
# page's headline reaches. Ranking teams by their punter would be a story
# about the reference board, not a roster.
SPECIAL_TEAMS = {"K", "DEF", "DST"}
# No more than this many stories of one kind in the top list. Five rosters
# built lopsided is one story, not five.
PER_KIND_CAP = 2
# Questionable tags are preseason wallpaper; four on top rooms is plenty.
QUESTIONABLE_CAP = 4


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " …"


def _week_of(issue_key: str) -> int | None:
    return int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None


def _story(headline: str, why: str, *, score: int, teams: list[str], evidence: list[str],
           kind: str, selected: bool = False) -> dict:
    return {"headline": headline, "why": why, "score": score, "teams": teams,
            "evidence": evidence, "kind": kind, "selected": selected}


# ------------------------------------------------------------------ signals

def _construction(profile: dict, names: dict[int, str], *, superflex: bool) -> list[dict]:
    """Rosters built lopsided: a top-two room beside a bottom-two room.

    The Superflex twist is real and league-specific: a bottom QB room costs
    a second starting slot every week, so it is scored higher there.
    """
    from leaguepage.team_analytics import is_rated

    n = profile.get("n") or 0
    if n < 4:
        return []
    out = []
    ranks = {pos: r for pos, r in (profile.get("ranks") or {}).items()
             if pos not in SPECIAL_TEAMS}
    for rid, nm in names.items():
        tops = [(pos, r[rid]) for pos, r in ranks.items()
                if r.get(rid) is not None and r[rid] <= 2 and is_rated(profile, pos, rid)]
        bottoms = [(pos, r[rid]) for pos, r in ranks.items()
                   if r.get(rid) is not None and r[rid] >= n - 1 and is_rated(profile, pos, rid)]
        if not tops or not bottoms:
            continue
        top_pos, top_rank = tops[0]
        bot_pos, bot_rank = bottoms[0]
        score = 40 + 5 * (bot_rank - top_rank)
        twist = ""
        if superflex and bot_pos == "QB":
            score += 10
            twist = " In Superflex that hole costs a second starting slot every week."
        out.append(_story(
            f"{nm}: #{top_rank} at {top_pos}, #{bot_rank} of {n} at {bot_pos}",
            f"A roster built to win at one position and lose at another.{twist}",
            score=score, teams=[nm], kind="construction",
            evidence=[f"positional ranks: {top_pos} #{top_rank}, {bot_pos} #{bot_rank} of {n}",
                      f"basis: {profile.get('stage')}"]))
    return out


def _injured_starters(storage: Storage, league: League, week: int | None, profile: dict,
                      names: dict[int, str], season: str) -> tuple[list[dict], list[str]]:
    """Starters carrying an out-type designation or a bye, on rooms that
    matter. Returns stories for the top rooms and watch lines for all."""
    from leaguepage.nfl_schedule import teams_on_bye

    if week is None:
        return [], []
    byes = teams_on_bye(season, week) or set()
    ranks = profile.get("ranks") or {}
    n = profile.get("n") or 0
    stories, watch = [], []
    for row in storage.get_matchups(league.league_id, week):
        rid = row.get("roster_id")
        for pid in (row.get("starters") or []):
            p = storage.get_player(pid) or {}
            status = (p.get("injury_status") or "").strip()
            on_bye = (p.get("team") or "") in byes
            if status not in OUT_STATUSES and status != "Questionable" and not on_bye:
                continue
            pos = (p.get("position") or "").upper()
            name = p.get("full_name") or pid
            rank = (ranks.get(pos) or {}).get(rid)
            tag = "BYE" if on_bye else status.upper()
            if status == "Questionable" and not on_bye and (not rank or rank > 2
                                                              or pos in SPECIAL_TEAMS):
                continue        # preseason marks half the league questionable
            watch.append(f"{tag}: {pos} {name}, starting for {names.get(rid, rid)}"
                         + (f" (#{rank} {pos} room)" if rank else ""))
            if (status in OUT_STATUSES or on_bye) and rank and rank <= max(2, n // 3):
                stories.append(_story(
                    f"{names.get(rid, rid)} is starting {name} ({tag}) on a #{rank} {pos} room",
                    "A strength on paper with a hole in it on Sunday; the room rank still "
                    "counts him.",
                    score=45 + (10 if rank <= 2 else 0), teams=[names.get(rid, str(rid))],
                    kind="availability",
                    evidence=[f"injury_status: {status or 'bye'}", f"{pos} room #{rank} of {n}",
                              "starter on the synced week lineup"]))
    hard = [w for w in watch if not w.startswith("QUESTIONABLE")]
    soft = [w for w in watch if w.startswith("QUESTIONABLE")][:QUESTIONABLE_CAP]
    return stories, hard + soft
def _move_stories(reviewed: list[dict]) -> list[dict]:
    out = []
    for r in reviewed:
        flags = r.get("flags") or []
        hard = [f for f in flags if not f.get("inferred")]
        soft = [f for f in flags if f.get("inferred")]
        score = 40 + 15 * len(hard) + 8 * len(soft) + int(30 * float(r.get("faab_share") or 0))
        why = "; ".join(f["label"] + ("" if not f.get("inferred") else " (inferred)")
                        for f in flags)
        ev = [e for f in flags for e in (f.get("evidence") or [])][:4]
        teams = list(r.get("teams") or [])
        out.append(_story(f"{' ↔ '.join(teams) if r.get('type') == 'trade' else ', '.join(teams)} — {r['line']}",
                          why + (f" Commissioner note: {r['note']}" if r.get("note") else ""),
                          score=score, teams=teams, kind="move", evidence=ev))
    return out


def _matchup_rows(computed: dict | None, profile: dict, names: dict[int, str],
                  injured_watch: list[str], selected_pairs: set | None = None) -> list[dict]:
    """Every matchup with what, if anything, makes it worth more than a
    capsule. Honest about boredom: a row with nothing beyond the baseline
    says so."""
    if not computed:
        return []
    n = profile.get("n") or 0
    ranks = profile.get("ranks") or {}
    rows = []
    ranks = {pos: r for pos, r in ranks.items() if pos not in SPECIAL_TEAMS}
    for sm in computed["scored"]:
        m = sm["matchup"]
        a, b = m["teams"]
        na, nb = names.get(a["roster_id"], f"Roster {a['roster_id']}"), names.get(b["roster_id"], f"Roster {b['roster_id']}")
        ci, sv = sm["competitive_importance"]["score"], sm["story_value"]["score"]
        comps = [c["label"] for c in sm["competitive_importance"]["components"]
                 + sm["story_value"]["components"] if not c["label"].startswith("baseline")]
        gaps = []
        for pos, r in ranks.items():
            ra, rb = r.get(a["roster_id"]), r.get(b["roster_id"])
            if ra is None or rb is None:
                continue
            if abs(ra - rb) >= max(3, n // 2):
                edge, other = (na, nb) if ra < rb else (nb, na)
                gaps.append(f"{pos}: {edge} #{min(ra, rb)} vs {other} #{max(ra, rb)}")
        watch = sorted((w for w in injured_watch if na in w or nb in w),
                       key=lambda w: w.startswith("QUESTIONABLE"))[:2]
        strong_angles = [ang for ang in (sm.get("angles") or [])
                         if ang.get("strength") == "strong" or "[strong]" in str(ang.get("title", ""))]
        rows.append({
            "selected": frozenset((a["roster_id"], b["roster_id"])) in (selected_pairs or set()),
            "label": f"{na} vs {nb}", "prominence": sm.get("recommended_prominence"),
            "ci": ci, "sv": sv, "interest": ci + sv - INTEREST_BASELINE,
            "why": comps[:3], "mismatches": gaps[:2], "availability": watch,
            "angles": len(strong_angles), "tags": list(sm.get("tags") or []),
            "boring": not comps and not gaps and not watch,
        })
    rows.sort(key=lambda r: (-r["interest"], -len(r["mismatches"]), r["label"]))
    return rows


def _reference_age(league: League) -> tuple[str | None, int | None]:
    from leaguepage.adp import load_adp_for_league

    src = load_adp_for_league(league)
    if src is None:
        return None, None
    when = getattr(src, "retrieved_at", None) or getattr(src, "imported_at", None)
    if not when:
        return getattr(src, "source_name", None) or league.adp_source, None
    try:
        then = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - then.astimezone(dt.timezone.utc)).days
    except ValueError:
        age = None
    return getattr(src, "source_name", None) or league.adp_source, age


# --------------------------------------------------------------------- data

def brief_data(storage: Storage, league: League, season: str, issue_key: str,
               *, candidates: list[dict] | None = None,
               base_dir: Path | None = None) -> dict:
    """Everything the brief prints, as data. Deterministic for a given DB."""
    from leaguepage import force_flow, identity_audit, pubqa
    from leaguepage import takes as takes_mod
    from leaguepage.issue_builder import assemble_issue
    from leaguepage.matchup_analysis import weekly_scores
    from leaguepage.team_analytics import positional_profile
    from leaguepage.team_names import resolve_public_names

    week = _week_of(issue_key)
    resolved = resolve_public_names(storage, league)
    names = {rid: (v["name"] or f"Roster {rid}") for rid, v in resolved.items()}
    scores = weekly_scores(storage, league.league_id, 18)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    profile = positional_profile(storage, league, weeks_played=weeks_played)
    positions = (storage.get_league(league.league_id) or {}).get("roster_positions") or []
    superflex = "SUPER_FLEX" in positions

    computed = None
    if week is not None:
        from leaguepage.matchup_packet import compute_week

        computed = compute_week(storage, league, week)

    decisions = storage.get_story_decisions(league.slug, season, issue_key)
    included = {cid for cid, d in decisions.items() if d.get("decision") == "include"}
    ignored = {cid for cid, d in decisions.items() if d.get("decision") == "ignore"}
    if candidates is None:
        try:
            from leaguepage.desk import candidates_for

            candidates = candidates_for(storage, league, season, issue_key)
        except Exception:
            candidates = []

    # --- market: what the flagging layer noticed, plus what he selected on
    #     the Story Board himself (joined on the candidate id, never the
    #     headline). His selection is a signal even when no flag fired.
    try:
        reviewed = force_flow.review(storage, league, season, week or 0, names=names) if week else []
    except Exception:
        reviewed = []
    try:
        from leaguepage.transaction_analysis import (analyze_transactions, describe_move,
                                                     story_candidate_id)

        seen = {r["txn_id"] for r in reviewed}
        for row in (analyze_transactions(storage, league, week) if week else []):
            if story_candidate_id(row) in included and row["txn_id"] not in seen:
                reviewed.append({
                    "txn_id": row["txn_id"], "week": row["week"], "type": row["type"],
                    "line": describe_move(row),
                    "teams": [names.get(r, f"Roster {r}") for r in row.get("rids", [])],
                    "faab": row.get("faab"), "faab_share": row.get("faab_share"),
                    "flags": [{"flag": "selected", "label": "On the Story Board",
                               "inferred": False, "why": "The Commissioner selected it.",
                               "evidence": [(row.get("rationale") or {}).get("text") or ""]}],
                    "note": None, "selected": True})
    except Exception:
        pass

    # --- stories
    stories: list[dict] = []
    stories += _move_stories(reviewed)
    avail_stories, watch = _injured_starters(storage, league, week, profile, names, season)
    stories += avail_stories
    stories += _construction(profile, names, superflex=superflex)
    # Matchups he already put on the Story Board carry his selection as a
    # signal; the join is the candidate's roster ids, never its headline.
    slug_to_rid = {}
    if computed:
        slug_to_rid = {slug: t["roster_id"] for slug, t in computed["analysis"]["teams"].items()}
    selected_pairs = set()
    for c in candidates or []:
        if c.get("candidate_id") in included and c.get("category") == "matchup":
            rids = [slug_to_rid.get(t) for t in (c.get("teams") or [])]
            if all(r is not None for r in rids) and len(rids) == 2:
                selected_pairs.add(frozenset(rids))
    matchups = _matchup_rows(computed, profile, names, watch, selected_pairs)
    for r in matchups[:2]:
        if r["interest"] > 0 or r["mismatches"]:
            stories.append(_story(
                f"Matchup: {r['label']}",
                "; ".join(r["why"] + r["mismatches"]) or "the week's most interesting pairing",
                score=30 + r["interest"] + 10 * len(r["mismatches"]) + (15 if r["selected"] else 0),
                teams=r["label"].split(" vs "), kind="matchup", selected=r["selected"],
                evidence=[f"CI {r['ci']}, SV {r['sv']}"] + r["mismatches"]))
    # takes with a reading, receipts on the record
    takes = []
    for t in storage.open_takes(league.slug, season):
        rec = t.get("recommended_status")
        label = takes_mod.STATUS_LABELS.get(rec, rec) if rec else None
        takes.append({"take_id": t["take_id"], "quote": t.get("quote") or "",
                      "subject": t.get("subject"), "context": t.get("context"),
                      "reading": label, "status": t.get("status")})
        if rec in (takes_mod.LEANING_RIGHT, takes_mod.LEANING_WRONG,
                   takes_mod.RESOLVED_RIGHT, takes_mod.RESOLVED_WRONG):
            stories.append(_story(
                f"Take {t['take_id']} is {label}: \"{_clip(t.get('quote') or '', 90)}\"",
                "A claim on the record now has evidence either way.",
                score=50, teams=[t.get("subject") or ""], kind="take",
                evidence=[f"engine reading: {label}"]))
    receipts = []
    try:
        from leaguepage import config as _cfg
        from leaguepage.receipts import live_receipts
        from leaguepage.site_build import _load_snapshots

        snaps = _load_snapshots(league, _cfg.PUBLISHED_DIR)
        for r in live_receipts(storage, league, season, week or 0, snaps, resolved)[:3]:
            receipts.append({"quote": r.get("quote"), "status": r.get("status"),
                             "subject": (r.get("subject_name") or names.get(r.get("subject_roster_id"))
                                         or r.get("subject") or "the league"),
                             "issue": r.get("issue_label")})
    except Exception:
        pass
    stories.sort(key=lambda s: (-s["score"], s["headline"]))
    top, per_kind = [], {}
    for st in stories:
        if per_kind.get(st["kind"], 0) >= PER_KIND_CAP:
            continue
        per_kind[st["kind"]] = per_kind.get(st["kind"], 0) + 1
        top.append(st)
        if len(top) >= TOP_STORIES:
            break

    # --- QA, coherence, gates
    try:
        qa = pubqa.check_issue(storage, league, season, issue_key, base_dir=base_dir, week=week)
    except Exception:
        qa = {"blockers": [], "warnings": [], "groups": []}
    all_findings = (qa.get("blockers") or []) + (qa.get("warnings") or [])
    collisions = [f for f in all_findings if f.get("category") in ("coherence",)
                  or f.get("title") in ("Player attributed to the wrong roster",
                                        "Team named by a name the paper does not use")]
    freshness = [f for f in all_findings if f.get("category") == "freshness"]
    try:
        assembled = assemble_issue(storage, league, season, issue_key, base_dir=base_dir, week=week)
        gate_warnings = list(assembled.get("warnings") or [])
    except Exception as exc:
        gate_warnings = [str(exc)]
    try:
        identity = [f for f in identity_audit.audit_league(storage, league)]
        identity = [f.as_dict() if hasattr(f, "as_dict") else f for f in identity]
    except Exception:
        identity = []

    # --- data watch
    src_name, src_age = _reference_age(league)
    unnamed = [rid for rid, v in resolved.items() if v["name"] is None]
    from leaguepage.nfl_schedule import describe_source, teams_on_bye

    byes = teams_on_bye(season, week) if week else None
    data_watch = []
    if src_name:
        data_watch.append(f"reference ranks: {src_name}"
                          + (f", {src_age} days old" if src_age is not None else "")
                          + (" — STALE" if src_age is not None and src_age > REFERENCE_STALE_DAYS else ""))
    else:
        data_watch.append("reference ranks: none on file")
    data_watch.append("projections: none on file (Sleeper's public API publishes none); "
                      "nothing here is a projection")
    data_watch.append(f"basis for player values: {profile.get('stage')}"
                      + (" — preseason evidence is the only evidence in week 1" if week == 1 else ""))
    if byes is None:
        data_watch.append("byes: no schedule on file for this season")
    elif byes:
        data_watch.append(f"byes this week: {', '.join(sorted(byes))} ({describe_source(season)})")
    else:
        data_watch.append(f"byes: none this week ({describe_source(season)})")
    data_watch += [f"unnamed roster: {rid}" for rid in unnamed]
    data_watch += [f"identity: {f.get('code')} — {f.get('detail') or f.get('title')}" for f in identity][:4]
    data_watch += watch[:8]
    data_watch += [f"freshness: {f.get('title')} ({f.get('module_key')})" for f in freshness][:4]

    # --- continuity
    prior_power = []
    if week and week >= 2:
        prev = {p["roster_id"]: p.get("rank") for p in storage.get_power_rankings(league.slug, season, f"week-{week-1:02d}")}
        cur = {p["roster_id"]: p.get("rank") for p in storage.get_power_rankings(league.slug, season, issue_key)}
        for rid, r in cur.items():
            if r and prev.get(rid) and abs(prev[rid] - r) >= 3:
                prior_power.append(f"{names.get(rid, rid)}: #{prev[rid]} → #{r}")
    prior_hardware = []
    if week and week >= 2:
        for key, d in storage.get_award_decisions(league.slug, season, f"week-{week-1:02d}").items():
            if d.get("decision") in ("awarded", "manual"):
                prior_hardware.append(f"{key}: {d.get('winner') or '(winner not recorded)'}")

    # --- scorecard
    n_matchups = len(matchups)
    specific = sum(1 for r in matchups if not r["boring"])
    strong_stories = [s for s in top if s["score"] >= 50]
    scorecard = {
        "Editorial focus": (f"strong — {len(strong_stories)} strong stories, "
                            f"{len(included)} selected on the Story Board"
                            if len(strong_stories) >= 3 and included
                            else f"review — {len(strong_stories)} strong stories surfaced, "
                                 f"{len(included)} selected on the Story Board"),
        "Evidence freshness": ("current" if (src_age is None or src_age <= REFERENCE_STALE_DAYS)
                               and not freshness
                               else "review — " + "; ".join(
                                   ([f"reference ranks {src_age} days old"] if src_age and src_age > REFERENCE_STALE_DAYS else [])
                                   + [f"{len(freshness)} freshness finding(s)"] if freshness else
                                   [f"reference ranks {src_age} days old"])),
        "Matchup specificity": (f"strong — {specific} of {n_matchups} have something beyond the baseline"
                                if n_matchups and specific * 2 >= n_matchups
                                else f"thin — {specific} of {n_matchups} have anything beyond the baseline"),
        "Cross-section overlap": "clean" if not collisions else f"{len(collisions)} review item(s)",
        "Continuity": f"{len(takes) + len(receipts) + len(prior_power) + len(prior_hardware)} follow-up(s) found",
        "Fact QA": ("blocked — " + str(len(qa.get("blockers") or [])) + " blocker(s)" if qa.get("blockers")
                    else ("clean" if not qa.get("warnings") else f"{len(qa.get('warnings'))} warning(s)")),
        "Publication gates": "ready" if not gate_warnings else f"blocked — {len(gate_warnings)} from assembly",
    }
    return {
        "league": league.display_name, "season": season, "issue_key": issue_key, "week": week,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "scorecard": scorecard, "top_stories": top, "matchups": matchups[:MATCHUPS_TO_WATCH],
        "market": reviewed[:6], "takes": takes[:6], "receipts": receipts,
        "prior_power": prior_power[:6], "prior_hardware": prior_hardware,
        "collisions": collisions[:8], "data_watch": data_watch,
        "gate_warnings": gate_warnings[:6],
        "source_note": (f"one reference source on file ({src_name}); disagreement between "
                        f"sources cannot be measured until a second is imported"
                        if src_name else "no reference source on file"),
    }


# ------------------------------------------------------------------- render

def render_brief(d: dict) -> str:
    a = []
    a.append(f"# Editorial Command Brief — {d['league']} {d['season']} {d['issue_key']}")
    a.append("")
    a.append("Private. Deterministic. Every line names its evidence; nothing here is")
    a.append("a recommendation to publish and nothing here reaches a reader.")
    a.append("")
    a.append("## SCORECARD")
    a.append("")
    for k, v in d["scorecard"].items():
        a.append(f"- {k}: {v}")
    a.append("")
    a.append("## TOP STORIES")
    a.append("")
    if not d["top_stories"]:
        a.append("- nothing above the noise floor this week; say so rather than inflate a line")
    for i, s in enumerate(d["top_stories"], 1):
        a.append(f"{i}. **{s['headline']}**  ({s['kind']}, {s['score']}"
                 + (", on the Story Board" if s.get("selected") else "") + ")")
        a.append(f"   why: {s['why']}")
        if s["evidence"]:
            a.append(f"   evidence: {'; '.join(str(e) for e in s['evidence'][:4])}")
    a.append("")
    a.append("## MATCHUPS TO WATCH")
    a.append("")
    for r in d["matchups"]:
        head = f"- **{r['label']}** — {r['prominence'] or '?'} (CI {r['ci']}, SV {r['sv']})"
        a.append(head)
        if r["boring"]:
            a.append("  nothing beyond the baseline: keep it short, no manufactured angle")
            continue
        for w in r["why"]:
            a.append(f"  why: {w}")
        for g in r["mismatches"]:
            a.append(f"  mismatch: {g}")
        for w in r["availability"]:
            a.append(f"  availability: {w}")
        if r["angles"]:
            a.append(f"  strong angles on file: {r['angles']}")
    a.append("")
    a.append("## MARKET / ROSTER MOVEMENT")
    a.append("")
    if not d["market"]:
        a.append("- nothing flagged")
    for r in d["market"]:
        teams = " ↔ ".join(r["teams"]) if r.get("type") == "trade" else ", ".join(r["teams"])
        flags = "; ".join(f["label"] + (" (inferred)" if f.get("inferred") else "") for f in r["flags"])
        a.append(f"- **{teams}** — {r['line']} (wk {r['week']}"
                 + (f", {r['faab']} FAAB" if r.get("faab") else "") + f"): {flags}")
        if r.get("note"):
            a.append(f"  Commissioner: {r['note']}")
    a.append("")
    a.append("## SOURCE DISAGREEMENT")
    a.append("")
    a.append(f"- {d['source_note']}")
    a.append("")
    a.append("## CONTINUITY")
    a.append("")
    if not (d["takes"] or d["receipts"] or d["prior_power"] or d["prior_hardware"]):
        a.append("- nothing on the record yet")
    for t in d["takes"]:
        a.append(f"- take {t['take_id']} ({t.get('context') or 'undated'}; engine: {t['reading'] or 'no reading yet'}): "
                 f"\"{_clip(t['quote'], 120)}\"")
    for r in d["receipts"]:
        a.append(f"- receipt ({r['status']}) on {r['subject']}, from {r['issue']}: \"{_clip(r['quote'] or '', 120)}\"")
    for p in d["prior_power"]:
        a.append(f"- power movement since last week: {p}")
    for h in d["prior_hardware"]:
        a.append(f"- last week's hardware: {h}")
    a.append("")
    a.append("## EDITORIAL COLLISIONS")
    a.append("")
    if not d["collisions"]:
        a.append("- clean")
    for f in d["collisions"]:
        a.append(f"- {f.get('title')} [{f.get('module_key')}]: {f.get('detail')}")
    a.append("")
    a.append("## DATA WATCH")
    a.append("")
    for w in d["data_watch"]:
        a.append(f"- {w}")
    if d["gate_warnings"]:
        a.append("")
        a.append("## PUBLICATION GATES")
        a.append("")
        for w in d["gate_warnings"]:
            a.append(f"- {w}")
    a.append("")
    a.append(f"_generated {d['generated_at']}_")
    a.append("")
    return "\n".join(a)


def top_story_lines(d: dict) -> list[str]:
    """The stories as Lowdown ammunition, for PREP.md."""
    out = ["## Top stories (Editorial Command Brief — deterministic, evidence attached)", ""]
    if not d["top_stories"]:
        out.append("- nothing above the noise floor this week")
    for s in d["top_stories"]:
        out.append(f"- {s['headline']} — {s['why']}")
        if s["evidence"]:
            out.append(f"  - evidence: {'; '.join(str(e) for e in s['evidence'][:3])}")
    return out


def build_command_brief(storage: Storage, league: League, season: str, issue_key: str,
                        *, candidates: list[dict] | None = None,
                        base_dir: Path | None = None) -> tuple[Path, dict]:
    d = brief_data(storage, league, season, issue_key, candidates=candidates, base_dir=base_dir)
    idir = issue_dir(league, season, issue_key, base_dir)
    idir.mkdir(parents=True, exist_ok=True)
    path = idir / "COMMAND_BRIEF.md"
    path.write_text(render_brief(d), encoding="utf-8")
    return path, d
