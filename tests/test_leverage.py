"""What this week is worth, and who to root for."""
from __future__ import annotations

from leaguepage.config import get_league
from leaguepage.leverage import (MIN_WEEKS, describe_stake, is_material,
                                 rooting_interest, week_leverage)

from season import populate_season

DISCO = get_league("disco")
SEASON = "2027"


def test_nothing_is_claimed_before_the_table_means_anything(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=MIN_WEEKS - 1,
                    current_week=MIN_WEEKS - 1, season=SEASON)
    assert week_leverage(storage, DISCO, MIN_WEEKS - 1, sims=100) is None
    assert rooting_interest(storage, DISCO, MIN_WEEKS - 1, 1, sims=100) == []


def test_nothing_is_claimed_without_a_schedule(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=8, current_week=8,
                    season=SEASON)
    for wk in range(9, 15):
        storage.save_matchups(DISCO.league_id, wk, [])
    assert week_leverage(storage, DISCO, 8, sims=100) is None


def test_every_team_playing_this_week_gets_a_number(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=8, current_week=8,
                    season=SEASON, seed=4)
    lev = week_leverage(storage, DISCO, 8, sims=1200)
    assert lev["week"] == 9
    # A team whose week is a foregone conclusion leaves too thin a slice on
    # one side to quote, and gets no number rather than a made-up one.
    assert 8 <= len(lev["teams"]) <= 12
    for rid, t in lev["teams"].items():
        assert 0.0 <= t["if_lose"] <= t["if_win"] <= 1.0, (rid, t)
        assert t["opponent"] != rid


def test_the_same_state_always_produces_the_same_number(storage):
    """A leverage figure that wobbles between builds is not quotable."""
    populate_season(storage, DISCO, teams=12, weeks_played=8, current_week=8,
                    season=SEASON, seed=4)
    a = week_leverage(storage, DISCO, 8, sims=300)
    b = week_leverage(storage, DISCO, 8, sims=300)
    assert a == b


def test_a_team_never_roots_for_its_own_game(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=8, current_week=8,
                    season=SEASON, seed=4)
    lev = week_leverage(storage, DISCO, 8, sims=1200)
    subject = next(iter(lev["teams"]))
    opponent = lev["teams"][subject]["opponent"]
    for r in rooting_interest(storage, DISCO, 8, subject, sims=1200):
        assert subject not in (r["root_for"], r["against"])
        assert opponent not in (r["root_for"], r["against"])


# --------------------------------------------------------- the wording

def test_a_formality_is_not_a_stake():
    """Five points of swing off a two-percent base is not something worth
    telling a reader about."""
    assert not is_material(0.12, 0.06)
    assert not is_material(0.90, 0.88)


def test_elimination_is_always_worth_saying():
    assert is_material(0.05, 0.00)
    assert describe_stake(0.05, 0.00) == "a loss ends it"


def test_a_team_already_in_is_not_told_a_win_settles_it():
    assert describe_stake(0.99, 0.93) == "matters"
    assert describe_stake(0.99, 0.40) == "a win settles it"


def test_the_wording_scales_with_the_swing():
    assert describe_stake(0.85, 0.45) == "decides most of it"
    assert describe_stake(0.60, 0.42) == "swings it hard"
    assert describe_stake(0.30, 0.24) == "matters"
