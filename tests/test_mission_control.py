"""The Desk home answers a question instead of listing inputs.

It used to open on a SYNC button and two lines per league: an issue status
word, a draft status word, a pick count. All true, none of it an answer to
"where was I and what is in my way".

These tests pin two things: the next action names the EARLIEST blocked step
(telling somebody to publish while four sections are empty is a button, not
guidance), and nothing on this screen decides anything.
"""
from __future__ import annotations

import re

import pytest

from leaguepage.config import get_league
from leaguepage.mission_control import (SYNC_STALE_HOURS, WORTH_A_LOOK,
                                        _age, _next_action)

DISCO = get_league("disco")


def _row(**over):
    base = {
        "league": DISCO, "season": "2026", "week": 4, "issue_key": "week-04",
        "sync_age": "2h ago", "sync_stale": False,
        "undecided": 0, "worth_a_look": 0,
        "sections": {"total": 8, "approved": 8, "drafted": 0, "empty": 0, "rows": []},
        "blockers": [], "issue_status": "generated",
    }
    base.update(over)
    return base


# --------------------------------------------------------- how old is it

@pytest.mark.parametrize("stamp,expected", [
    (None, "never"),
    ("not-a-date", "unknown"),
])
def test_a_missing_or_broken_timestamp_says_so(stamp, expected):
    assert _age(stamp)[1] == expected


def test_age_is_reported_in_the_units_a_person_thinks_in():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert _age((now - timedelta(minutes=10)).isoformat())[1] == "just now"
    assert _age((now - timedelta(hours=5)).isoformat())[1] == "5h ago"
    assert _age((now - timedelta(days=1, hours=2)).isoformat())[1] == "1 day ago"
    assert _age((now - timedelta(days=3)).isoformat())[1] == "3 days ago"


def test_a_stale_sync_is_measured_against_the_stated_threshold():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    fresh, _ = _age((now - timedelta(hours=SYNC_STALE_HOURS - 1)).isoformat())
    stale, _ = _age((now - timedelta(hours=SYNC_STALE_HOURS + 1)).isoformat())
    assert fresh < SYNC_STALE_HOURS <= stale


# ------------------------------------------------------- the next action

def test_stale_data_comes_before_everything_else():
    """Triaging an inbox built from three-day-old rosters is work done twice."""
    a = _next_action(_row(sync_stale=True, sync_age="3 days ago",
                          worth_a_look=5,
                          sections={"total": 8, "approved": 0, "drafted": 0,
                                    "empty": 8, "rows": []}))
    assert a["text"] == "Sync Sleeper"
    assert a["href"].endswith("#syncpanel"), "a link to the page you are on is not an action"


def test_triage_comes_before_writing():
    a = _next_action(_row(worth_a_look=3, undecided=9,
                          sections={"total": 8, "approved": 0, "drafted": 0,
                                    "empty": 8, "rows": []}))
    assert "Triage 3" in a["text"]
    assert a["href"] == "/commissioner/inbox"
    assert "9 undecided" in a["why"]


def test_writing_comes_before_approving():
    a = _next_action(_row(sections={"total": 8, "approved": 2, "drafted": 3,
                                    "empty": 3, "rows": []}))
    assert a["text"] == "Write 3 empty sections"
    assert a["href"].endswith("/issue/week-04/room")


def test_approving_comes_before_publishing():
    a = _next_action(_row(sections={"total": 8, "approved": 5, "drafted": 3,
                                    "empty": 0, "rows": []}))
    assert a["text"] == "Review and approve 3 sections"
    assert a["href"].endswith("/review")


def test_publishing_is_only_offered_when_it_would_actually_work():
    """A publish link while four sections are empty is a button that refuses."""
    blocked = _next_action(_row(sections={"total": 8, "approved": 4, "drafted": 0,
                                          "empty": 4, "rows": []}))
    assert "publish" not in blocked["text"].lower()

    ready = _next_action(_row())
    assert ready["text"] == "Preview and publish week 4"
    assert ready["href"].endswith("/publish")


def test_a_blocker_is_named_before_publish_is_offered():
    a = _next_action(_row(blockers=["Module 'lowdown' is not approved"]))
    assert "blocker" in a["text"]
    assert "lowdown" in a["why"]


def test_a_quiet_week_says_so_rather_than_inventing_work():
    a = _next_action(_row(sections={"total": 0, "approved": 0, "drafted": 0,
                                    "empty": 0, "rows": []}))
    assert a["why"] == "nothing is waiting on you"


def test_singular_and_plural_are_both_written_out():
    one = _next_action(_row(worth_a_look=1, undecided=1,
                            sections={"total": 1, "approved": 0, "drafted": 0,
                                      "empty": 1, "rows": []}))
    assert "1 item in" in one["text"]
    many = _next_action(_row(worth_a_look=2, undecided=2,
                             sections={"total": 1, "approved": 0, "drafted": 0,
                                       "empty": 1, "rows": []}))
    assert "2 items in" in many["text"]


def test_the_noise_floor_is_the_stated_one():
    below = _next_action(_row(undecided=4, worth_a_look=0))
    assert "Triage" not in below["text"]
    assert WORTH_A_LOOK > 0


# ------------------------------------------------- the publication boundary

DECIDES = re.compile(r"\b(approve|publish|include|exclude|set_|write|delete)\s*\(",
                     re.I)


def test_the_screen_reads_and_never_decides():
    """Deployment authority does not extend to approving sections or
    publishing issues, and a status screen is exactly where that would creep
    in as a convenience."""
    import pathlib

    src = pathlib.Path("leaguepage/mission_control.py").read_text(encoding="utf-8")
    body = "\n".join(line for line in src.split("\n")
                     if not line.strip().startswith(("#", "*", '"'))
                     and "assemble_issue" not in line)
    assert not DECIDES.search(body), DECIDES.search(body).group(0)
    for banned in ("set_issue_status", "set_module", "publish_assembled_issue",
                   "revise_issue", "storage.set_", "record_decision"):
        assert banned not in src, banned


def test_it_never_enforces_when_it_asks_what_would_block():
    """Asking assemble_issue what is wrong must not be the thing that
    freezes a snapshot."""
    import pathlib

    src = pathlib.Path("leaguepage/mission_control.py").read_text(encoding="utf-8")
    assert "enforce=False" in src
    assert "enforce=True" not in src
