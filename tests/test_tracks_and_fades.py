"""Tracks and Fades nominate a team whose record misrepresents it.

The gap test alone did not do that. An undefeated team has a win
percentage of 1.000, so any all-play under .800 cleared the 0.2 threshold
and a 70% all-play — elite — was nominated as a Fade for being elite. The
same arithmetic nominated every winless team as a Track. Both are
properties of the extremes, not stories about the teams.
"""
from __future__ import annotations

from leaguepage.config import get_league
from leaguepage.weekly_signals import tracks_and_fades

LG = get_league("disco")


def _team(slug, wins, losses, ap_wins, ap_losses, ap_weeks, scores):
    games = ap_wins + ap_losses
    return {
        "roster_id": int(slug.split("-")[-1]),
        "record": {"wins": wins, "losses": losses},
        "all_play": {"wins": ap_wins, "losses": ap_losses, "ties": 0,
                     "pct": round(ap_wins / games, 3) if games else None,
                     "games": games, "weeks": ap_weeks},
        "recent_scores": scores,
        "streak": None,
        # A Track that is already top-2 just restates the standings, so the
        # nomination is gated on it; these fixtures sit outside that.
        "standing": 6,
    }


def _analysis(teams, weeks_played=4):
    return {"weeks_played": weeks_played, "season": "2026", "week": weeks_played,
            "teams": teams}


def _hooks(entries):
    return [f for e in entries for f in e.get("facts", [])]


def test_an_elite_all_play_is_not_a_fade_for_being_elite(db=None):
    """4-0 with a .700 all-play is a good team that has also won its games.
    The old rule faded it because 1.000 - 0.700 clears 0.2."""
    a = _analysis({"a-4": _team("a-4", 4, 0, 19, 8, 4, [110, 112, 115, 118])})
    _tracks, fades = tracks_and_fades(None, LG, 4, a)
    assert not any("all-play" in f for f in _hooks(fades))


def test_a_winless_team_scoring_badly_is_not_a_track():
    """0-4 with a .250 all-play is losing because it is not scoring."""
    a = _analysis({"b-5": _team("b-5", 0, 4, 7, 20, 4, [70, 72, 71, 69])})
    tracks, _fades = tracks_and_fades(None, LG, 4, a)
    assert not any("all-play" in f for f in _hooks(tracks))


def test_the_real_divergence_still_fires_in_both_directions():
    a = _analysis({
        # wins games it should lose: middling scoring, gaudy record
        "lucky-6": _team("lucky-6", 4, 0, 10, 17, 4, [95, 96, 95, 97]),
        # loses games it should win: strong scoring, ugly record
        "robbed-7": _team("robbed-7", 0, 4, 17, 10, 4, [120, 119, 121, 120]),
    })
    tracks, fades = tracks_and_fades(None, LG, 4, a)
    assert any("outruns all-play" in f for f in _hooks(fades))
    assert any("runs well ahead of the" in f for f in _hooks(tracks))


def test_a_record_and_an_all_play_over_different_spans_are_not_compared():
    """`record` is season-to-date; all-play here runs through the previous
    week. Citing a four-game record against three weeks of all-play and
    calling the difference a divergence compares two different seasons."""
    a = _analysis({"c-8": _team("c-8", 4, 0, 10, 17, 2, [95, 96, 95, 97])})
    tracks, fades = tracks_and_fades(None, LG, 4, a)
    assert not any("all-play" in f for f in _hooks(tracks) + _hooks(fades))


def test_the_trend_reads_the_whole_window_not_just_its_ends():
    """First-against-last threw away every week between them, so one big
    Sunday at either end decided the verdict."""
    # ends are flat, the middle spikes: not a trend either way
    a = _analysis({"d-9": _team("d-9", 2, 2, 13, 14, 4, [100, 160, 155, 101])})
    tracks, fades = tracks_and_fades(None, LG, 4, a)
    assert not any("scoring" in f for f in _hooks(tracks) + _hooks(fades))

    # a genuine climb across the window
    b = _analysis({"e-10": _team("e-10", 2, 2, 13, 14, 4, [80, 84, 120, 124])})
    tracks, _ = tracks_and_fades(None, LG, 4, b)
    assert any("scoring rising" in f for f in _hooks(tracks))
