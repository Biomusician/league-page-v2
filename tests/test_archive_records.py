"""Six seasons of titles, read out of newsletters nobody thought to index.

The ledger is the only thing in the archive worth parsing by machine, and
these tests pin why: the pattern that works, the thirty-one lookalikes it
must refuse, the corrections it has to honour, and the league boundary it
must not cross.
"""
from __future__ import annotations

import pytest

from leaguepage.archive_records import (_rows, ledger_note, resolve_handles,
                                        season_ledger)
from leaguepage.config import get_league
from leaguepage.storage import Storage

DISCO = get_league("disco")
SURFEIT = get_league("surfeit")

LEDGER = """The Lowdown

Seasons Past

2021
Winner: Babe / Loser: HOP (2)

2020
Winner: The Dude / Loser: PITCH

2019
Winner: McLovin / Loser: HOP
"""


def _issue(storage, *, issue_id_hint, season, week, body, league="disco",
           title=None):
    storage.upsert_archive_issue(
        league_slug=league, season=season, week=week,
        title=title or f"{season} Disco Week {week}",
        source_path=f"archive/{league}/{season}-week-{week:02d}.md",
        body=body, dating_confidence="high", dating_note="")


# ------------------------------------------------------------ the pattern

def test_the_block_parses_and_the_repeat_markers_are_stripped():
    rows = _rows(LEDGER)
    assert rows == [("2021", "Babe", "HOP"),
                    ("2020", "The Dude", "PITCH"),
                    ("2019", "McLovin", "HOP")]


def test_a_draft_review_row_is_not_a_championship():
    """There are thirty-one other `Winner:` lines in the corpus and every one
    is a draft-review row. A looser pattern reads those as titles."""
    assert _rows("Round 4: Winner: George Kittle (DIP x2) "
                 "Loser: Aaron Rodgers (The Dude x2)") == []
    assert _rows("2021\nRound 4: Winner: Kittle / Loser: Rodgers") == []


def test_an_in_season_snapshot_with_no_result_yet_is_skipped():
    assert _rows("2026\nWinner: TBD / Loser: TBD") == []
    assert _rows("2026\nWinner: Babe / Loser: TBD") == []


def test_a_year_that_is_not_a_season_does_not_open_a_row():
    assert _rows("1999\nWinner: Somebody / Loser: Nobody") == []


# ----------------------------------------------------------- the assembly

def test_the_ledger_reads_every_season_from_the_archive(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2021", week=3, body=LEDGER)
        rows = season_ledger(s, DISCO)
    assert [r["season"] for r in rows] == ["2021", "2020", "2019"]
    assert rows[0]["champion"] == "Babe" and rows[0]["last_place"] == "HOP"


def test_the_latest_issue_that_asserts_a_season_wins(tmp_path):
    """The block runs in every issue, so a later one correcting an earlier
    one is the author's own last word."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2022", week=2,
               body="Seasons Past\n\n2021\nWinner: Wrong / Loser: HOP\n")
        _issue(s, issue_id_hint=2, season="2022", week=9,
               body="Seasons Past\n\n2021\nWinner: Babe / Loser: HOP\n")
        rows = season_ledger(s, DISCO)
    assert len(rows) == 1
    assert rows[0]["champion"] == "Babe"
    assert rows[0]["issue_title"].endswith("Week 9")


def test_every_row_names_the_issue_it_came_from(tmp_path):
    """No stat without provenance, and the link is the provenance."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2021", week=3, body=LEDGER)
        rows = season_ledger(s, DISCO)
    for r in rows:
        assert r["issue_id"] and r["href"] == f"archive/a{r['issue_id']}/index.html"


def test_a_ledger_carried_forward_reports_a_season_with_no_issue(tmp_path):
    """2024 has no archived issue at all. The 2025 masthead carried it, and
    that is the only record of it anywhere."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2025", week=7,
               body="Seasons Past\n\n2024\nWinner: DIP (x2) / Loser: Babe\n")
        rows = season_ledger(s, DISCO)
    assert rows[0]["season"] == "2024" and rows[0]["champion"] == "DIP"


# ------------------------------------------------------- league boundary

def test_a_defunct_third_league_never_becomes_this_league_s_history(tmp_path):
    """Big Daddy AF was a different league on a different scoring scale.
    Its records are not these records, in either direction."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2019", week=4, body=LEDGER,
               league="daddy")
        assert season_ledger(s, DISCO) == []
        assert season_ledger(s, SURFEIT) == []


def test_the_surfeit_has_no_ledger_and_that_is_correct(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        _issue(s, issue_id_hint=1, season="2021", week=3, body=LEDGER)
        assert season_ledger(s, SURFEIT) == []
        assert ledger_note([]) == ""


# ---------------------------------------------------------- identity

def _rows_for_resolve():
    return [{"season": "2021", "champion": "Babe", "last_place": "HOP",
             "issue_id": 1, "issue_title": "t", "href": "h"}]


def test_a_confirmed_alias_becomes_a_link():
    teams = [{"roster_id": 4, "team_slug": "corn-fed-fatties-babe",
              "manager_keys": ["m1"]}]
    managers = {"m1": {"aliases": ["Babe"]}}
    out = resolve_handles(_rows_for_resolve(), teams, managers)
    assert out[0]["champion_slug"] == "corn-fed-fatties-babe"
    assert out[0]["champion_rid"] == 4


def test_an_unconfirmed_alias_is_printed_but_never_linked():
    """An inferred alias is a guess, and a guess rendered as "this is who won
    in 2021" is exactly the claim this system exists not to make."""
    teams = [{"roster_id": 4, "team_slug": "somebody", "manager_keys": ["m1"]}]
    managers = {"m1": {"unverified_aliases": ["Babe"]}}
    out = resolve_handles(_rows_for_resolve(), teams, managers)
    assert out[0]["champion"] == "Babe"
    assert out[0]["champion_slug"] is None


def test_a_name_nobody_claims_still_appears():
    out = resolve_handles(_rows_for_resolve(), [], {})
    assert out[0]["last_place"] == "HOP"
    assert out[0]["last_place_slug"] is None


def test_the_note_says_where_the_numbers_came_from():
    note = ledger_note([{"season": "2024"}, {"season": "2019"}])
    assert "2019" in note and "2024" in note
    assert "Sleeper" in note
