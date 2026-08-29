"""Pull everything both leagues need from Sleeper and persist it locally.

Adapted from Fantasy Bot's sync.py, extended with draft ingestion. Weekly runs
re-fetch only what can change; the ~5MB players dictionary is refreshed at most
once every 20 hours (Sleeper asks for at most once a day).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from leaguepage.config import LEAGUES, League
from leaguepage.sleeper import SleeperClient, SleeperAPIError
from leaguepage.storage import Storage

logger = logging.getLogger(__name__)

PLAYERS_REFRESH_INTERVAL = dt.timedelta(hours=20)


@dataclass
class SyncResult:
    league: League
    ok: bool
    rosters: int = 0
    users: int = 0
    drafts: int = 0
    picks: int = 0
    weeks_synced: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def ensure_players_cached(client: SleeperClient, storage: Storage, *, force: bool = False) -> None:
    last_updated = storage.players_last_updated()
    is_stale = force or last_updated is None or (
        dt.datetime.now(dt.timezone.utc) - last_updated > PLAYERS_REFRESH_INTERVAL
    )
    if not is_stale:
        logger.info("Players cache fresh as of %s (%d players)", last_updated, storage.player_count())
        return
    logger.info("Fetching /players/nfl (~5MB)...")
    players = client.get_all_players()
    if not players:
        if storage.player_count() > 0:
            logger.warning("Players fetch returned empty; keeping stale cache")
            return
        raise RuntimeError("Players fetch returned no data and no local cache exists")
    storage.save_players(players)
    logger.info("Cached %d players", len(players))


def current_week(client: SleeperClient) -> tuple[int, str]:
    """Return (week, season_type). Sleeper's week counter runs during the
    preseason too ("week 3" in August means preseason week 3), so outside the
    regular season we clamp to fantasy week 1."""
    state = client.get_nfl_state()
    season_type = state.get("season_type") or "regular"
    if season_type != "regular":
        return 1, season_type
    week = state.get("display_week") or state.get("week") or 1
    return max(1, int(week)), season_type


def sync_league(
    client: SleeperClient,
    storage: Storage,
    league: League,
    *,
    week: int,
    weeks_back: int = 1,
) -> SyncResult:
    result = SyncResult(league, ok=False)
    try:
        league_data = client.get_league(league.league_id)
        if league_data is None:
            result.error = "league not found (404)"
            return result
        storage.save_league(league.league_id, league_data)

        rosters = client.get_rosters(league.league_id)
        storage.save_rosters(league.league_id, rosters)
        result.rosters = len(rosters)

        users = client.get_league_users(league.league_id)
        storage.save_league_users(league.league_id, users)
        result.users = len(users)

        for r in rosters:
            if not r.get("owner_id"):
                result.warnings.append(f"roster {r.get('roster_id')} has no owner")

        for draft in client.get_drafts(league.league_id):
            storage.save_draft(draft)
            result.drafts += 1
            picks = client.get_draft_picks(draft["draft_id"])
            if picks:
                storage.save_draft_picks(draft["draft_id"], picks)
                result.picks += len(picks)
            elif draft.get("status") == "complete":
                result.warnings.append(f"draft {draft['draft_id']} complete but no picks returned")

        for w in range(max(1, week - weeks_back + 1), week + 1):
            try:
                storage.save_matchups(league.league_id, w, client.get_matchups(league.league_id, w))
                storage.save_transactions(league.league_id, w, client.get_transactions(league.league_id, w))
                result.weeks_synced.append(w)
            except SleeperAPIError as exc:
                result.warnings.append(f"week {w} skipped: {exc}")

        result.ok = True
        return result
    except SleeperAPIError as exc:
        logger.error("Failed syncing %s: %s", league.slug, exc)
        result.error = str(exc)
        return result


def sync_all(
    storage: Storage,
    *,
    weeks_back: int = 1,
    refresh_players: bool = True,
) -> list[SyncResult]:
    client = SleeperClient()
    if refresh_players:
        ensure_players_cached(client, storage)
    week, season_type = current_week(client)
    storage.set_meta("current_week", str(week))
    storage.set_meta("season_type", season_type)
    results = []
    for league in LEAGUES:
        logger.info("Syncing %s (%s)...", league.slug, league.league_id)
        results.append(sync_league(client, storage, league, week=week, weeks_back=weeks_back))
    return results
