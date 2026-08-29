"""Story Memory retrieval — the allowed editorial-memory set per matchup.

Hard scoping rule (from Jonathan): league memory stays in its league. A Disco
gag never reaches Surfeit copy merely because the same person plays in both,
and vice versa. Cross-league archive material is retrievable ONLY for a
manager whose entry sets `allow_cross_league_callbacks: true` — an explicit
commissioner marking, and even then the source league is labeled.

Callback ranking favors one strong callback over several weak ones:
relevance (FTS rank order), dating confidence, prior-reuse count (from the
editorial_usage log), and league scope. Issues without high dating confidence
are excluded from date-sensitive claims by marking them 'date_unreliable'.
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.editorial import confirmed_aliases
from leaguepage.storage import Storage

# league slug -> archive league slugs that count as the SAME league's memory
ARCHIVE_SCOPE = {
    "disco": ["disco"],
    "surfeit": ["surfeit"],
    # "daddy" is Big Daddy AF's archive; it belongs to neither current league
}


def _search_terms(team: dict, managers: dict[str, dict]) -> list[tuple[str, dict]]:
    """(term, manager_entry) pairs — confirmed aliases and the team name."""
    terms: list[tuple[str, dict]] = []
    for key in team.get("manager_keys", []):
        m = managers.get(key) or {}
        for alias in confirmed_aliases(m):
            terms.append((alias, m))
    if team.get("team_name"):
        terms.append((team["team_name"], {}))
    return [(t, m) for t, m in terms if t and len(t) >= 4]


def retrieve_callbacks(
    storage: Storage,
    league_slug: str,
    teams: list[dict],
    managers: dict[str, dict],
    *,
    season: str,
    limit: int = 3,
) -> list[dict]:
    in_scope = set(ARCHIVE_SCOPE.get(league_slug, [league_slug]))
    hits: list[dict] = []
    seen: set[int] = set()
    for team in teams:
        for term, manager in _search_terms(team, managers):
            cross_ok = bool(manager.get("allow_cross_league_callbacks"))
            try:
                results = storage.search_archive(f'"{term}"', limit=4)
            except Exception:
                continue
            for h in results:
                if h["issue_id"] in seen:
                    continue
                same_league = h["league_slug"] in in_scope
                if not same_league and not cross_ok:
                    continue  # hard scoping rule
                seen.add(h["issue_id"])
                issue = storage.get_archive_issue(h["issue_id"]) or {}
                confident = (issue.get("dating_confidence") or "high") == "high"
                reuse = storage.usage_count(league_slug, season, "callback",
                                            evidence.archive_ref(h["issue_id"]))
                hits.append({
                    "issue_id": h["issue_id"],
                    "title": h["title"],
                    "source_league": h["league_slug"],
                    "season": h["season"],
                    "week": h["week"],
                    "matched_term": term,
                    "team_slug": team["team_slug"],
                    "snippet": h["snippet"],
                    "cross_league": not same_league,
                    "date_unreliable": not confident,
                    "prior_reuse": reuse,
                    "evidence": evidence.archive_ref(h["issue_id"]),
                })
    # rank: unreused first, date-reliable first, same-league first, FTS order preserved
    hits.sort(key=lambda h: (h["prior_reuse"], h["date_unreliable"], h["cross_league"]))
    ranked = hits[:limit]
    for i, h in enumerate(ranked):
        h["strength"] = "strong" if (i == 0 and not h["date_unreliable"] and h["prior_reuse"] == 0) else "supporting"
    return ranked


def retrieve_takes(storage: Storage, league_slug: str, season: str, teams: list[dict]) -> list[dict]:
    slugs = {t["team_slug"] for t in teams}
    keys = {k for t in teams for k in t.get("manager_keys", [])}
    return [
        t for t in storage.all_takes(league_slug, season)
        if t["subject"] in slugs | keys
    ]


def retrieve_awards(storage: Storage, league_slug: str, season: str, teams: list[dict]) -> list[dict]:
    slugs = {t["team_slug"] for t in teams}
    out = []
    for workflow in ("draft",):
        for key, d in storage.get_award_decisions(league_slug, season, workflow).items():
            if d.get("decision") in ("awarded", "manual") and d.get("winner") in slugs:
                out.append({"workflow": workflow, "award_key": key, "winner": d["winner"],
                            "note": d.get("note")})
    return out


def recurring_bits(teams: list[dict], managers: dict[str, dict]) -> list[dict]:
    out = []
    for team in teams:
        for key in team.get("manager_keys", []):
            m = managers.get(key) or {}
            if m.get("sensitivity") == "do_not_use":
                continue
            for bit in m.get("recurring_bits") or []:
                out.append({"team_slug": team["team_slug"], "bit": bit,
                            "sensitivity": m.get("sensitivity", "fair_game"),
                            "evidence": evidence.manager_ref(key)})
    return out


def story_memory_for_matchup(
    storage: Storage,
    league_slug: str,
    season: str,
    matchup: dict,
    managers: dict[str, dict],
) -> dict:
    teams = matchup["teams"]
    return {
        "callbacks": retrieve_callbacks(storage, league_slug, teams, managers, season=season),
        "takes": retrieve_takes(storage, league_slug, season, teams),
        "awards": retrieve_awards(storage, league_slug, season, teams),
        "recurring_bits": recurring_bits(teams, managers),
        "scoping_rule": (
            "League memory only. Cross-league material appears solely for "
            "managers explicitly marked allow_cross_league_callbacks, and is "
            "labeled with its source league."
        ),
    }
