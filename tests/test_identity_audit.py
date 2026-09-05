"""Owners have to line up across the four stores that describe them.

Sleeper holds the rosters and the users; `team_names` holds the public name
the Commissioner confirmed; `editorial/managers.json` holds the callsign and
the per-league roster binding. They join on the Sleeper user id, and until
now nothing checked that the join held. A roster changing hands, a team
renamed on Sleeper, a callsign edited into one store and not another --
every one of those reads as normal on every screen.

The audit reconciles; it never guesses. Two similar strings are not evidence
that two records are the same person, so no finding here is produced by
comparing names for similarity: each one names two stores and the stable id
they disagree about.
"""
from __future__ import annotations

import pytest

from leaguepage.config import get_league
from leaguepage.identity_audit import (BLOCKER, WARNING, audit, audit_league,
                                       callsigns_in, spelling_findings)
from leaguepage.storage import Storage

from fixtures import populate_league

SEASON = "2026"
LG = get_league("surfeit")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.sqlite3"
    with Storage(path) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        yield s


def _managers(**leagues_by_key):
    return {key: {"sleeper_user_id": uid, "leagues": {"surfeit": {"roster_id": rid}}}
            for key, (uid, rid) in leagues_by_key.items()}


def _owner_of(s, rid):
    return next(r for r in s.get_rosters(LG.league_id) if r["roster_id"] == rid)["owner_id"]


# ------------------------------------------------------------ callsigns

def test_a_callsign_is_read_out_of_the_public_name():
    """There is no column for it. It lives inside the name by convention,
    which is exactly why it drifts."""
    assert callsigns_in("Wild SeeKats (Seebass/Kats)") == ["Seebass", "Kats"]
    assert callsigns_in("Los Bandidos (Bandit)") == ["Bandit"]


def test_a_team_with_no_parenthetical_declares_no_callsign():
    """Several teams are just a name. That is not a fault."""
    assert callsigns_in("Dave") == []
    assert callsigns_in(None) == []
    assert callsigns_in("") == []


# ------------------------------------------------------------ clean league

def test_a_consistent_league_reports_nothing(db):
    mgrs = {f"m{r['roster_id']}": {"sleeper_user_id": r["owner_id"],
                                   "leagues": {"surfeit": {"roster_id": r["roster_id"]}}}
            for r in db.get_rosters(LG.league_id)}
    for rid in range(1, 11):
        db.set_public_team_name("surfeit", rid, f"Team {rid} (C{rid})")
    assert audit_league(db, LG, mgrs) == []


# ------------------------------------------------------------ the disagreements

def test_a_manager_bound_to_a_roster_he_does_not_own(db):
    """The failure a rename between syncs actually produces."""
    mgrs = {"someone": {"sleeper_user_id": _owner_of(db, 1),
                        "leagues": {"surfeit": {"roster_id": 2}}}}
    codes = {f.code for f in audit_league(db, LG, mgrs)}
    assert "manager-roster-mismatch" in codes
    f = next(f for f in audit_league(db, LG, mgrs) if f.code == "manager-roster-mismatch")
    assert f.severity == BLOCKER and f.roster_id == 2


def test_one_sleeper_user_under_two_manager_keys(db):
    uid = _owner_of(db, 1)
    mgrs = {"a": {"sleeper_user_id": uid, "leagues": {"surfeit": {"roster_id": 1}}},
            "b": {"sleeper_user_id": uid, "leagues": {"surfeit": {"roster_id": 1}}}}
    f = next(f for f in audit_league(db, LG, mgrs) if f.code == "duplicate-manager")
    assert f.severity == BLOCKER
    assert "a" in f.detail and "b" in f.detail


def test_more_managers_claim_a_roster_than_own_it(db):
    """Co-management is legitimate; three claims on a one-owner roster is
    not, and the count is what says so rather than the names."""
    mgrs = {k: {"sleeper_user_id": _owner_of(db, 1),
                "leagues": {"surfeit": {"roster_id": 1}}} for k in ("a", "b", "c")}
    codes = [f.code for f in audit_league(db, LG, mgrs)]
    assert "roster-over-claimed" in codes


def test_two_rosters_publishing_under_one_name(db):
    """They would collide their URLs and their team pages."""
    db.set_public_team_name("surfeit", 1, "The Same Name")
    db.set_public_team_name("surfeit", 2, "The Same Name")
    f = next(f for f in audit_league(db, LG, {}) if f.code == "duplicate-public-name")
    assert f.severity == BLOCKER


def test_one_callsign_on_two_rosters(db):
    db.set_public_team_name("surfeit", 1, "Alpha (Bandit)")
    db.set_public_team_name("surfeit", 2, "Beta (Bandit)")
    f = next(f for f in audit_league(db, LG, {}) if f.code == "callsign-on-two-rosters")
    assert f.severity == WARNING and "Bandit" in f.detail


def test_a_roster_with_no_public_name_cannot_publish(db):
    """The fixture league names every roster through Sleeper, so the
    condition has to be made: a manager who has set no fantasy team name
    and has no commissioner override is a roster that cannot publish."""
    users = db.get_league_users(LG.league_id)
    users[0]["metadata"] = {}
    db.save_league_users(LG.league_id, users)
    found = [f for f in audit_league(db, LG, {}) if f.code == "no-public-name"]
    assert found, "an unnamed roster must be reported"
    assert all(f.severity == BLOCKER for f in found)


def test_a_roster_no_manager_record_binds(db):
    codes = {f.code for f in audit_league(db, LG, {})}
    assert "roster-unclaimed" in codes


def test_findings_never_come_from_comparing_names(db):
    """Two similar strings are not evidence that two records are the same
    person. Every finding names a stable id, and none is produced by
    fuzzy matching."""
    import inspect

    from leaguepage import identity_audit

    src = inspect.getsource(identity_audit)
    for banned in ("difflib", "SequenceMatcher", "fuzz", "levenshtein",
                   "get_close_matches", "startswith(name"):
        assert banned not in src, banned


def test_blockers_sort_ahead_of_warnings(db):
    db.set_public_team_name("surfeit", 1, "Alpha (Bandit)")
    db.set_public_team_name("surfeit", 2, "Beta (Bandit)")
    rows = audit(db, [LG], {})
    severities = [r["severity"] for r in rows]
    assert severities == sorted(severities, key=lambda x: 0 if x == BLOCKER else 1)


# ------------------------------------------------------------ the spelling

def test_a_superseded_callsign_spelling_is_reported(db):
    db.set_public_team_name("surfeit", 7, "Wild SeeKats (Seabass/Kats)")
    rows = spelling_findings(db, [LG], "Seebass", "Seabass")
    assert len(rows) == 1
    assert rows[0]["roster_id"] == 7 and rows[0]["code"] == "superseded-callsign"


def test_the_canonical_spelling_reports_nothing(db):
    db.set_public_team_name("surfeit", 7, "Wild SeeKats (Seebass/Kats)")
    assert spelling_findings(db, [LG], "Seebass", "Seabass") == []


def test_the_spelling_check_reads_the_public_name_and_not_prose(db):
    """A quotation that genuinely contained the old spelling stays as
    written. This check has no way to reach prose at all."""
    import inspect

    from leaguepage import identity_audit

    src = inspect.getsource(identity_audit.spelling_findings)
    assert "resolve_public_names" in src
    for reaches_prose in ("read_text", "open(", "issue_dir", "sections"):
        assert reaches_prose not in src, reaches_prose
