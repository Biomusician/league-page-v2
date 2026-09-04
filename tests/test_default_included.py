"""The recurring spine of the newsletter starts included.

The week's job is to say what does not belong this time, not to opt each
section back in one at a time. Two things follow from that:

* a module with no saved decision is INCLUDED, except the three opt-in
  sidebar features, which run only when there is an edition to run;
* a module that has nothing to say does not remove itself. It stays in and
  says so, because whether an empty section is worth writing or worth
  dropping is the Commissioner's call and hiding it hides the decision.

`Intel Prep of the Fantasy Space` is the case that made this concrete: it
used to exclude itself before week 5 on the grounds that early-season
playoff leverage is fake precision. That reasoning is right and it is now
printed on an included section instead of being acted on silently.
"""
from __future__ import annotations

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.issue_builder import (EMPTY_SECTION_NOTE, OPT_IN_MODULES,
                                      module_states)
from leaguepage.storage import Storage

from fixtures import populate_league

SEASON = "2027"


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        for lg in (get_league("disco"), get_league("surfeit")):
            populate_league(s, lg, teams=10, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
    return db


def _states(db, slug, week=1, issue="week-01"):
    with Storage(db) as s:
        return {m["module_key"]: m
                for m in module_states(s, get_league(slug), SEASON, issue, week=week)}


@pytest.mark.parametrize("slug", ["disco", "surfeit"])
def test_a_new_weekly_issue_starts_with_the_recurring_spine_included(env, slug):
    st = _states(env, slug)
    for key in ("lowdown", "hardware", "ctp", "power", "tracks", "fades",
                "forceflow", "blackbox", "intel"):
        if key not in st:
            continue          # not a module this league runs
        assert st[key]["included"], f"{slug}: {key} should start included"


@pytest.mark.parametrize("slug", ["disco", "surfeit"])
def test_only_the_opt_in_sidebar_features_start_excluded(env, slug):
    st = _states(env, slug)
    excluded = {k for k, m in st.items() if not m["included"]}
    assert excluded <= OPT_IN_MODULES, f"{slug} excluded something unexpected: {excluded}"


def test_intel_no_longer_removes_itself_before_week_five(env):
    """It stays in and explains why it is thin, rather than disappearing."""
    st = _states(env, "surfeit")
    intel = st["intel"]
    assert intel["included"] is True
    assert intel["empty"] is True
    assert "needs 5+ played weeks" in intel["detail"]
    assert "omitted" not in intel["detail"]


def test_an_included_section_with_nothing_in_it_is_flagged_not_dropped(env):
    st = _states(env, "surfeit")
    empties = [k for k, m in st.items() if m["empty"]]
    assert empties, "the fixture should have unwritten sections"
    for k in empties:
        assert st[k]["included"], f"{k} was flagged empty but also dropped"


def test_the_note_is_the_one_the_commissioner_reads():
    assert EMPTY_SECTION_NOTE == "No meaningful material this week — consider excluding"


def test_a_written_section_is_not_flagged_empty(env, tmp_path):
    ed = ib.EDITORIAL_DIR / SEASON / "surfeit" / "week-01" / "sections"
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "tracks.md").write_text("## Tracks\n\nReal copy.\n", encoding="utf-8")
    st = _states(env, "surfeit")
    assert st["tracks"]["included"] and st["tracks"]["empty"] is False


def test_an_explicit_exclusion_survives_a_refresh(env):
    """Sync must never re-include something he took out on purpose."""
    with Storage(env) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON,
                           issue_key="week-01", module_key="fades", included=0)
    st = _states(env, "surfeit")
    assert st["fades"]["included"] is False
    # ...and re-reading does not drift it back
    assert _states(env, "surfeit")["fades"]["included"] is False


def test_an_explicit_inclusion_of_an_opt_in_feature_also_survives(env):
    with Storage(env) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON,
                           issue_key="week-01", module_key="custom", included=1)
    assert _states(env, "surfeit")["custom"]["included"] is True


def test_a_historical_issue_is_not_rewritten(env):
    """Old issues keep the decisions they were published with."""
    with Storage(env) as s:
        for key in ("power", "tracks", "fades"):
            s.set_issue_module(league_slug="surfeit", season=SEASON,
                               issue_key="week-01", module_key=key, included=0)
    st = _states(env, "surfeit")
    assert [st[k]["included"] for k in ("power", "tracks", "fades")] == [False, False, False]
