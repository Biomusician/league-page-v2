"""Thin wrapper around the public, unauthenticated Sleeper API.

Adapted from Fantasy Bot's sleeper_tool/client.py. Docs: https://docs.sleeper.com/
Every endpoint is read-only and needs no key. We retry transient failures and
keep a soft rate limit (Sleeper asks to stay under 1000 req/min).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from leaguepage.config import SLEEPER_BASE_URL

logger = logging.getLogger(__name__)

USER_AGENT = "league-page/0.1 (personal league site; contact via Sleeper app)"


class SleeperAPIError(RuntimeError):
    """Raised when the Sleeper API returns an error we can't recover from."""


class SleeperClient:
    def __init__(
        self,
        base_url: str = SLEEPER_BASE_URL,
        session: requests.Session | None = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.5,
        min_request_interval_seconds: float = 0.06,  # ~1000/min ceiling
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.min_request_interval_seconds = min_request_interval_seconds
        self._last_request_at = 0.0

    # -- low level -----------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_request_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, *, allow_404: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=20)
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, exc)
                time.sleep(self.retry_backoff_seconds * attempt)
                continue

            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code == 429:
                logger.warning("Rate limited on %s, backing off", url)
                time.sleep(self.retry_backoff_seconds * attempt * 2)
                continue
            if 500 <= resp.status_code < 600:
                last_exc = SleeperAPIError(f"{resp.status_code} from {url}")
                time.sleep(self.retry_backoff_seconds * attempt)
                continue
            if not resp.ok:
                raise SleeperAPIError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")

            if not resp.content:
                return None
            return resp.json()

        raise SleeperAPIError(f"GET {url} failed after {self.max_retries} attempts") from last_exc

    # -- users ---------------------------------------------------------

    def get_user(self, username_or_id: str) -> dict:
        return self._get(f"/user/{username_or_id}")

    # -- leagues -------------------------------------------------------

    def get_league(self, league_id: str) -> dict | None:
        return self._get(f"/league/{league_id}", allow_404=True)

    def get_rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters") or []

    def get_league_users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users") or []

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}") or []

    def get_transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}") or []

    def get_winners_bracket(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/winners_bracket") or []

    def get_losers_bracket(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/losers_bracket") or []

    # -- drafts --------------------------------------------------------

    def get_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts") or []

    def get_draft(self, draft_id: str) -> dict | None:
        return self._get(f"/draft/{draft_id}", allow_404=True)

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks") or []

    # -- players -------------------------------------------------------

    def get_all_players(self, sport: str = "nfl") -> dict[str, dict]:
        """~5MB dictionary of every player Sleeper tracks. Cached — see ingest.py."""
        return self._get(f"/players/{sport}") or {}

    # -- state ---------------------------------------------------------

    def get_nfl_state(self) -> dict:
        return self._get("/state/nfl")
