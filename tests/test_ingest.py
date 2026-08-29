from __future__ import annotations

from leaguepage.ingest import current_week


class StubClient:
    def __init__(self, state):
        self._state = state

    def get_nfl_state(self):
        return self._state


def test_preseason_clamps_to_week_one():
    week, season_type = current_week(StubClient({"season_type": "pre", "week": 3, "display_week": 3}))
    assert week == 1 and season_type == "pre"


def test_regular_season_uses_display_week():
    week, season_type = current_week(StubClient({"season_type": "regular", "week": 6, "display_week": 7}))
    assert week == 7 and season_type == "regular"


def test_missing_state_defaults_sane():
    week, season_type = current_week(StubClient({"season_type": "regular"}))
    assert week == 1
