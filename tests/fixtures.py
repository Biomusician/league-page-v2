"""Synthetic league/draft builders — no network, deterministic."""
from __future__ import annotations

from leaguepage.adp import ADPSource
from leaguepage.config import League

POSITIONS = ["RB", "WR", "QB", "TE", "WR", "RB"]
NFL_TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]

TEST_LEAGUE = League(
    slug="testleague", display_name="TEST LEAGUE", league_id="TEST123",
    theme="disco", subtitle="Synthetic", adp_source="",
)


def player_name(i: int) -> str:
    return f"Player Number{i}"


def league_payload(league: League, teams: int, season: str = "2026", status: str = "in_season") -> dict:
    return {
        "league_id": league.league_id,
        "name": league.display_name.title(),
        "season": season,
        "status": status,
        "total_rosters": teams,
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN", "BN"],
        "scoring_settings": {"rec": 0.5, "pass_td": 4.0},
    }


def populate_league(
    storage,
    league: League = TEST_LEAGUE,
    *,
    teams: int = 10,
    rounds: int = 3,
    picks: str = "complete",     # complete | partial | none
    co_managed_roster: int | None = None,
    season: str = "2026",
) -> dict:
    """Create league + users + rosters + one draft (snake). Returns draft dict."""
    storage.save_league(league.league_id, league_payload(league, teams, season))
    users = []
    rosters = []
    for i in range(1, teams + 1):
        users.append({
            "user_id": f"u{i}", "display_name": f"Manager{i}",
            "metadata": {"team_name": f"Team {i}"},
        })
        roster = {"roster_id": i, "owner_id": f"u{i}"}
        if co_managed_roster == i:
            users.append({"user_id": f"u{i}co", "display_name": f"CoManager{i}", "metadata": {}})
            roster["co_owners"] = [f"u{i}co"]
        rosters.append(roster)
    storage.save_league_users(league.league_id, users)
    storage.save_rosters(league.league_id, rosters)

    draft = {
        "draft_id": f"D-{league.league_id}",
        "league_id": league.league_id,
        "season": season,
        "status": "complete" if picks == "complete" else ("drafting" if picks == "partial" else "pre_draft"),
        "type": "snake",
        "settings": {"rounds": rounds},
    }
    storage.save_draft(draft)

    if picks != "none":
        total = teams * rounds
        if picks == "partial":
            total = teams * rounds // 2
        rows = []
        for pick_no in range(1, total + 1):
            rnd = (pick_no - 1) // teams + 1
            idx = (pick_no - 1) % teams
            slot = idx + 1 if rnd % 2 == 1 else teams - idx  # snake
            rows.append({
                "pick_no": pick_no,
                "round": rnd,
                "draft_slot": slot,
                "roster_id": slot,
                "player_id": f"p{pick_no}",
                "picked_by": f"u{slot}",
                "metadata": {
                    "first_name": "Player",
                    "last_name": f"Number{pick_no}",
                    "position": POSITIONS[pick_no % len(POSITIONS)],
                    "team": NFL_TEAMS[pick_no % len(NFL_TEAMS)],
                },
            })
        storage.save_draft_picks(draft["draft_id"], rows)
    return draft


def default_pairs(teams: int) -> list[tuple[int, int]]:
    return [(i, i + 1) for i in range(1, teams + 1, 2)]


def populate_matchups(
    storage,
    league: League = TEST_LEAGUE,
    *,
    week: int,
    teams: int = 10,
    pairs: list[tuple[int, int]] | None = None,
    scores: dict[int, float] | None = None,
    players_points: dict[int, dict[str, float]] | None = None,
    starters: dict[int, list[str]] | None = None,
) -> None:
    """Write one week of matchup rows. scores omitted/0 = unplayed week."""
    rows = []
    for mid, (a, b) in enumerate(pairs or default_pairs(teams), start=1):
        for rid in (a, b):
            rows.append({
                "roster_id": rid,
                "matchup_id": mid,
                "points": (scores or {}).get(rid, 0.0),
                "starters": (starters or {}).get(rid, []),
                "players_points": (players_points or {}).get(rid, {}),
            })
    storage.save_matchups(league.league_id, week, rows)


def set_records(storage, league: League = TEST_LEAGUE, records: dict[int, tuple] | None = None) -> None:
    """records: roster_id -> (wins, losses, fpts). Rewrites roster settings."""
    rosters = storage.get_rosters(league.league_id)
    for r in rosters:
        if r["roster_id"] in (records or {}):
            w, l, fpts = records[r["roster_id"]]
            r["settings"] = {"wins": w, "losses": l, "ties": 0,
                             "fpts": int(fpts), "fpts_decimal": int(round((fpts % 1) * 100))}
    storage.save_rosters(league.league_id, rosters)


def make_adp(entries: dict[int, float], teams: int = 10, rounds: int = 3) -> ADPSource:
    """Reference ranks for synthetic players. entries maps pick_no -> rank;
    unlisted players get rank == their pick_no (delta 0); a pick_no mapped to
    None is deliberately absent from the source (unmatched)."""
    players = []
    for pick_no in range(1, teams * rounds + 1):
        rank = entries.get(pick_no, float(pick_no))
        if rank is None:
            continue
        players.append({
            "name": player_name(pick_no),
            "position": POSITIONS[pick_no % len(POSITIONS)],
            "team": NFL_TEAMS[pick_no % len(NFL_TEAMS)],
            "rank": rank,
        })
    return ADPSource(
        source_key="test_ref", source_name="Test Reference Ranks", kind="test",
        scoring_format="half_ppr", retrieved_at="2026-08-29T00:00:00", note="",
        players=players,
    )
