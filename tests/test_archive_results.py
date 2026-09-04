"""Results recovered from records that were printed before the games.

The archive states almost no results as results — the matchup blocks are
previews. But a preview prints each team's record and so does the next
week's, so the week between them is recoverable from the difference.

The whole design bet is that the failure mode is a MISS, never a wrong
result. These tests are mostly about the ways a real corpus lies: a
copy-paste error that puts one team in two matchups, a record typed
`(63-9)`, a win probability written `52-48` instead of `52/48`, a shouty
week that renames a franchise, and a roster drafted by a proxy who then
appears as a team.

Every one of those is in the live 2021 corpus. Against it this recovers 52
games across 11 weeks, and all 52 were re-derived by a separately written
parser reading the raw markdown.
"""
from __future__ import annotations

from leaguepage.archive_results import (canonical, coverage_note, learn_aliases,
                                        reconstruct, standings_rows,
                                        title_tension, week_pairings)


def _issue(week, lines, issue_id=None):
    return {"week": week, "issue_id": issue_id or week,
            "title": f"Week {week}", "body": "\n\n".join(lines)}


def _wk(week, *pairs):
    """`pairs` of ((name, w, l), (name, w, l)) as a preview issue."""
    return _issue(week, [f"Some Label: {a} ({aw}-{al}) vs {b} ({bw}-{bl}) 60/40"
                         for (a, aw, al), (b, bw, bl) in pairs])


def test_a_result_is_recovered_from_the_records_around_it():
    out = reconstruct([
        _wk(5, (("HOP", 2, 2), ("The Dude", 2, 2)), (("Babe", 1, 3), ("Pitch", 1, 3))),
        _wk(6, (("HOP", 3, 2), ("The Dude", 2, 3)), (("Babe", 2, 3), ("Pitch", 1, 4))),
    ])
    got = {(r["winner_name"], r["loser_name"]) for r in out["results"]}
    assert got == {("HOP", "The Dude"), ("Babe", "Pitch")}
    assert out["unresolved"] == []


def test_a_probability_written_with_a_dash_is_never_read_as_a_record():
    """The live week 8 header ends `52-48` instead of `52/48`. Records only
    ever come from inside parentheses, which is what makes that safe."""
    p = week_pairings("EMCO (1-6) vs Juan (0-7) 52-48", week=8, aliases={})
    assert p["records"] == {"emco": (1, 6), "juan": (0, 7)}
    assert p["pairs"] == [("emco", "juan")]


def test_a_typed_record_that_cannot_be_true_takes_only_its_own_row():
    """`Juan (63-9)` in week 13. There is no way to know which half is
    wrong, so the row goes and the rest of the week stands."""
    p = week_pairings(
        "Sealed (7-5) vs Juan (63-9) 70/30\nDIP (5-7) vs Pitch (1-11) 76/24",
        week=13, aliases={})
    assert p["pairs"] == [("dip", "pitch")]
    assert "juan" not in p["records"] and "sealed" not in p["records"]
    assert p["usable"]
    assert any("does not match the week" in w for w in p["why"])


def test_a_week_that_contradicts_itself_is_not_used_at_all():
    """The live week 8 puts EMCO in two different matchups. Which line is
    the copy-paste error is unknowable, so the week contributes nothing."""
    p = week_pairings(
        "EMCO (1-6) vs Juan (0-7)\nEMCO (5-2) vs McLovin (5-2)", week=8, aliases={})
    assert not p["usable"]
    assert "appears in two matchups" in p["why"][0]

    out = reconstruct([
        _wk(7, (("EMCO", 5, 1), ("Juan", 0, 6))),
        _issue(8, ["EMCO (1-6) vs Juan (0-7)", "EMCO (5-2) vs McLovin (5-2)"]),
        _wk(9, (("EMCO", 6, 2), ("Juan", 0, 8))),
    ])
    # neither the week before nor the week after can be resolved through it
    assert out["results"] == []
    assert [s["week"] for s in out["skipped"]] == [8]


def test_case_does_not_split_a_franchise():
    assert canonical("PITCH") == canonical("Pitch") == "pitch"
    out = reconstruct([
        _wk(2, (("PITCH", 1, 0), ("Babe", 0, 1))),
        _wk(3, (("Pitch", 2, 0), ("Babe", 0, 2))),
    ])
    assert len(out["results"]) == 1
    assert out["results"][0]["winner"] == "pitch"


def test_a_proxy_name_is_learned_from_the_corpus_not_assumed():
    """A roster drafted on someone else's behalf appears under two names.
    Week 1 writes it `The Dude/Glory`, which is the author saying they are
    one team. Without that, `Glory` becomes an eleventh team in a ten-team
    league and breaks the real team's week-over-week delta."""
    issues = [
        _issue(1, ["Defending Champ: The Dude/Glory (0-0) vs Babe (0-0) 60/40"]),
        _wk(2, (("Glory", 1, 0), ("Babe", 0, 1))),
        _wk(3, (("The Dude", 2, 0), ("Babe", 0, 2))),
    ]
    aliases = learn_aliases([i["body"] for i in issues])
    assert aliases["glory"] == "the dude"

    out = reconstruct(issues)
    assert set(out["standings"]) == {"the dude", "babe"}
    assert out["standings"]["the dude"] == {"wins": 2, "losses": 0}


def test_anything_but_one_win_and_one_loss_produces_nothing():
    """Both teams gaining a win, or a team gaining two, means the issues
    disagree about what happened. A miss is the correct outcome."""
    out = reconstruct([
        _wk(4, (("A", 2, 1), ("B", 2, 1))),
        _wk(5, (("A", 3, 1), ("B", 3, 1))),      # both won: impossible
    ])
    assert out["results"] == []
    assert out["unresolved"] and "records moved" in out["unresolved"][0]["why"]


def test_a_team_missing_from_the_next_issue_is_unresolved_not_guessed():
    out = reconstruct([
        _wk(4, (("A", 2, 1), ("B", 2, 1))),
        _issue(5, ["A (3-1) vs C (3-1) 60/40"]),
    ])
    assert out["results"] == []
    assert out["unresolved"][0]["why"] == "a team is not in the next issue"


def test_non_consecutive_issues_recover_nothing():
    out = reconstruct([
        _wk(4, (("A", 2, 1), ("B", 2, 1))),
        _wk(9, (("A", 6, 2), ("B", 3, 5))),
    ])
    assert out["results"] == []


def test_the_coverage_note_names_the_weeks_it_could_not_recover():
    out = reconstruct([
        _wk(1, (("A", 0, 0), ("B", 0, 0))),
        _wk(2, (("A", 1, 0), ("B", 0, 1))),
        _issue(3, ["A (2-0) vs B (0-2)", "A (1-1) vs C (1-1)"]),   # contradicts
        _wk(4, (("A", 3, 0), ("B", 0, 3))),
        _wk(5, (("A", 4, 0), ("B", 0, 4))),
    ])
    note = coverage_note(out)
    assert "Weeks 2, 3 are missing" in note
    assert "left out rather than guessed at" in note


def test_standings_rank_on_percentage_because_teams_played_different_counts():
    """A team whose week was dropped has fewer games, and 5-4 is a better
    season than 6-5."""
    rows = standings_rows({
        "standings": {"a": {"wins": 5, "losses": 4}, "b": {"wins": 6, "losses": 5}},
        "names": {"a": "A", "b": "B"},
    })
    assert [r["name"] for r in rows] == ["A", "B"]
    assert rows[0]["games"] == 9 and rows[1]["games"] == 11


def test_the_page_says_when_the_regular_season_leader_did_not_win():
    rows = standings_rows({"standings": {"a": {"wins": 9, "losses": 2},
                                         "b": {"wins": 6, "losses": 5}},
                           "names": {"a": "McLovin", "b": "Babe"}})
    ledger = [{"season": "2021", "champion": "Babe", "last_place": "HOP"}]
    line = title_tension(rows, "2021", ledger)
    assert "McLovin led the recovered weeks at 9-2" in line
    assert "Babe as the 2021 champion" in line

    agree = title_tension(rows, "2021",
                          [{"season": "2021", "champion": "McLovin"}])
    assert "and won the title" in agree

    assert title_tension(rows, "2019", ledger) is None       # no ledger row
    assert title_tension([], "2021", ledger) is None         # nothing recovered


def test_a_shared_lead_is_not_an_upset():
    rows = standings_rows({"standings": {"a": {"wins": 9, "losses": 2},
                                         "b": {"wins": 9, "losses": 2}},
                           "names": {"a": "A", "b": "B"}})
    assert title_tension(rows, "2021", [{"season": "2021", "champion": "B"}]) is None


# ------------------------------------------------ scope and privacy

def test_a_league_never_sees_another_leagues_reconstruction(tmp_path):
    """Disco jokes do not leak into Surfeit and neither do Disco's results.
    The Surfeit has no archive corpus at all, so its page must carry
    nothing rather than borrow the other league's."""
    from leaguepage.archive_results import season_results
    from leaguepage.config import get_league
    from leaguepage.storage import Storage

    with Storage(tmp_path / "t.sqlite3") as db:
        for wk in range(1, 9):
            db.upsert_archive_issue(
                league_slug="disco", season="2021", week=wk,
                title=f"Week {wk}", source_path=f"archive/disco/w{wk}.md",
                body=f"Label: A ({wk - 1}-0) vs B (0-{wk - 1}) 60/40",
                dating_confidence="high", dating_note="")
        assert [r["season"] for r in season_results(db, get_league("disco"))] == ["2021"]
        assert season_results(db, get_league("surfeit")) == []


def test_a_season_with_an_unpublishable_name_does_not_ship_at_all():
    """A manager who leaves keeps his results and loses the public team
    name that made his handle printable. Half a season would misstate every
    record in it."""
    from leaguepage.archive_results import drop_private_results

    rec = {"season": "2021", "names": {"a": "McLovin", "b": "Babe"}}
    assert drop_private_results([rec], []) == [rec]
    assert drop_private_results([rec], ["Nobody"]) == [rec]
    assert drop_private_results([rec], ["Babe"]) == []


def test_a_thin_season_is_not_published_as_a_record():
    """Three scattered weeks is a curiosity, not a season."""
    from leaguepage.archive_results import MIN_WEEKS, season_results
    from leaguepage.config import get_league
    from leaguepage.storage import Storage
    import tempfile, pathlib as _p

    with tempfile.TemporaryDirectory() as d:
        with Storage(_p.Path(d) / "t.sqlite3") as db:
            for wk in range(1, MIN_WEEKS + 1):     # one short of MIN_WEEKS+1 usable
                db.upsert_archive_issue(
                    league_slug="disco", season="2019", week=wk,
                    title=f"W{wk}", source_path=f"archive/disco/x{wk}.md",
                    body=f"Label: A ({wk - 1}-0) vs B (0-{wk - 1}) 60/40",
                    dating_confidence="high", dating_note="")
            got = season_results(db, get_league("disco"))
    assert got == [], "a season under the floor must not publish"
