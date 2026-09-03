"""Live history and receipts: quality, provenance, repetition protection.

The failure mode this file guards is shipping callbacks to satisfy coverage.
A newsletter archive is half prose and half scoreboard, and a naive full-text
hit returns the scoreboard half — "Geronimo Allison, WR88 (Babe x3) Round 14:
Winner: Mark Andrews" is a real match and a worthless quote.
"""
from __future__ import annotations

import pytest

from leaguepage import history, receipts
from leaguepage.config import get_league
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

LEAGUE = get_league("disco")

PROSE = ("Corn-Fed Fatties will lock in a first-round BYE if he beats The Dude "
         "this week, which is the only thing standing between him and a "
         "genuinely restful December.")
TABLE = ("Geronimo Allison, WR88 (Babe x3) Round 14: Winner: Mark Andrews, TE2 "
         "(Babe) Loser: Donte Moncrief, WR189 (Fingers) Round 15: Winner")
INJURY = "The Finals Babe vs McLovin 70/30 Babe INJ: NSTR McLovin INJ: NSTR"
FRAGMENT = "8 Cooper Kupp (Anomalies) The Toilet Bowl In the battle to the bottom"


# ------------------------------------------------------- quality filter

@pytest.mark.parametrize("text", [PROSE,
                                  "Juan, you can't make the playoffs anymore, "
                                  "but you can ruin someone else's hopes and dreams."])
def test_real_sentences_pass(text):
    assert history.reads_as_prose(text)


@pytest.mark.parametrize("text", [TABLE, INJURY, FRAGMENT, "", "Short one here.",
                                  "7-4 3-9 8-2 6-6 5-7 4-8 70/30 60/40 55/45 12-2"])
def test_table_fragments_are_rejected(text):
    assert not history.reads_as_prose(text)


# ------------------------------------------------------- archive quotes

@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LEAGUE, teams=4, rounds=2, season="2026")
        s.set_meta("current_week", "2")
        populate_matchups(s, LEAGUE, week=1, teams=4,
                          pairs=[(1, 2), (3, 4)],
                          scores={1: 110.2, 2: 99.0, 3: 80.0, 4: 120.0})
        s.upsert_archive_issue(
            league_slug="disco", season="2021", week=13, title="Disco 13",
            source_path="archive/disco/13.md",
            body=("Round 3: Winner: Somebody (Team 1 x2) Loser: Nobody\n\n"
                  + PROSE + "\n\n"
                  "Team 1 has spent the whole year telling anyone who would "
                  "listen that the schedule is out to get him personally.\n"),
            dating_confidence="high", dating_note="")
    with Storage(db) as s:
        yield s


def test_archive_quote_returns_a_whole_sentence_not_a_window(env):
    issue = env.list_archive_issues("disco")[0]
    quote = history.archive_quote(env, issue["issue_id"], ["Team 1"])
    assert quote
    assert quote.endswith(".")
    assert "Round 3:" not in quote
    assert "Winner:" not in quote


def test_archive_quote_is_none_when_only_tables_match(env):
    issue = env.list_archive_issues("disco")[0]
    assert history.archive_quote(env, issue["issue_id"], ["Geronimo"]) is None


# ------------------------------------------------------ matchup history

def _matchup(a=1, b=2, meetings=None):
    meetings = meetings or []
    wins_a = sum(1 for m in meetings if m["winner"] == a)
    wins_b = sum(1 for m in meetings if m["winner"] == b)
    return {"matchup_slug": f"t{a}-vs-t{b}",
            "teams": [{"roster_id": a}, {"roster_id": b}],
            "h2h": {"record": {a: wins_a, b: wins_b}, "meetings": meetings,
                    "last_meeting": meetings[-1] if meetings else None}}


NAMES = {1: "Team 1", 2: "Team 2", 3: "Team 3", 4: "Team 4"}


def test_no_history_means_no_history_section(env):
    assert history.matchup_history(env, LEAGUE, "2026", 2, _matchup(),
                                   {"callbacks": []}, NAMES) == []


def test_head_to_head_and_last_meeting_carry_provenance(env):
    m = _matchup(meetings=[{"week": 1, "points": {1: 110.2, 2: 99.0}, "winner": 1}])
    items = history.matchup_history(env, LEAGUE, "2026", 2, m, {}, NAMES)
    kinds = [i["kind"] for i in items]
    assert "Head to head" in kinds and "Last meeting" in kinds
    for i in items:
        assert i["source"]
    assert any("Team 1 leads 1–0" in i["text"] for i in items)


def test_an_archive_callback_quotes_prose_and_links_to_the_issue(env):
    issue = env.list_archive_issues("disco")[0]
    items = history.matchup_history(
        env, LEAGUE, "2026", 2, _matchup(),
        {"callbacks": [{"issue_id": issue["issue_id"], "title": "Disco 13",
                        "season": "2021", "week": 13, "matched_term": "Team 1",
                        "strength": "strong", "date_unreliable": False,
                        "snippet": TABLE}]},
        NAMES)
    assert len(items) == 1
    assert items[0]["kind"] == "From the archive"
    assert items[0]["href"] == f"archive/a{issue['issue_id']}/index.html"
    assert "Disco 13" in items[0]["source"] and "2021" in items[0]["source"]
    assert "Round 14" not in items[0]["text"]      # the FTS snippet was rejected


def test_a_private_handle_never_leaves_the_archive(env):
    issue = env.list_archive_issues("disco")[0]
    items = history.matchup_history(
        env, LEAGUE, "2026", 2, _matchup(),
        {"callbacks": [{"issue_id": issue["issue_id"], "title": "Disco 13",
                        "season": "2021", "week": 13, "matched_term": "Team 1",
                        "strength": "strong", "date_unreliable": False,
                        "snippet": ""}]},
        NAMES, private_handles=["Team 1"])
    assert all(i["kind"] != "From the archive" for i in items)


def test_undated_archive_issues_are_not_quoted(env):
    issue = env.list_archive_issues("disco")[0]
    items = history.matchup_history(
        env, LEAGUE, "2026", 2, _matchup(),
        {"callbacks": [{"issue_id": issue["issue_id"], "title": "Disco 13",
                        "season": "2021", "week": 13, "matched_term": "Team 1",
                        "strength": "strong", "date_unreliable": True,
                        "snippet": PROSE}]},
        NAMES)
    assert items == []


def test_history_is_capped_at_two_items(env):
    issue = env.list_archive_issues("disco")[0]
    m = _matchup(meetings=[{"week": 1, "points": {1: 110.2, 2: 99.0}, "winner": 1}])
    items = history.matchup_history(
        env, LEAGUE, "2026", 2, m,
        {"callbacks": [{"issue_id": issue["issue_id"], "title": "Disco 13",
                        "season": "2021", "week": 13, "matched_term": "Team 1",
                        "strength": "strong", "date_unreliable": False,
                        "snippet": PROSE}]},
        NAMES)
    assert len(items) <= history.MAX_ITEMS


# ------------------------------------------------ repetition protection

def test_a_callback_used_recently_steps_aside(env):
    issue = env.list_archive_issues("disco")[0]
    m = _matchup(meetings=[{"week": 1, "points": {1: 110.2, 2: 99.0}, "winner": 1}])
    memory = {"callbacks": [{"issue_id": issue["issue_id"], "title": "Disco 13",
                             "season": "2021", "week": 13, "matched_term": "Team 1",
                             "strength": "strong", "date_unreliable": False,
                             "snippet": PROSE}]}
    week3 = history.matchup_history(env, LEAGUE, "2026", 3, m, memory, NAMES)
    assert any(i["kind"] == "From the archive" for i in week3)
    week4 = history.matchup_history(env, LEAGUE, "2026", 4, m, memory, NAMES)
    archive_ids = [i["item_id"] for i in week4 if i["kind"] == "From the archive"]
    assert not archive_ids, "the same callback ran two weeks running"


def test_rebuilding_the_same_week_is_idempotent(env):
    m = _matchup(meetings=[{"week": 1, "points": {1: 110.2, 2: 99.0}, "winner": 1}])
    first = history.matchup_history(env, LEAGUE, "2026", 3, m, {}, NAMES)
    second = history.matchup_history(env, LEAGUE, "2026", 3, m, {}, NAMES)
    assert [i["item_id"] for i in first] == [i["item_id"] for i in second]


def test_receipts_repetition_memory_is_week_keyed(env):
    receipts.record_shown(env, "disco", "2026", "abc", 3)
    receipts.record_shown(env, "disco", "2026", "abc", 3)
    assert receipts.shown_weeks(env, "disco", "2026")["abc"] == [3]
    receipts.record_shown(env, "disco", "2026", "abc", 4)
    assert receipts.shown_weeks(env, "disco", "2026")["abc"] == [3, 4]


# --------------------------------------------------------- receipt rules

def _claim(quote, players=(), positions=(), rid=1):
    return {"claim_id": "c1", "roster_id": rid, "quote": quote,
            "players": list(players), "positions": list(positions),
            "issue_key": "draft", "issue_label": "Draft Issue",
            "href": "2026/draft/index.html", "section_title": "The Lowdown"}


def test_a_departed_player_puts_a_claim_under_pressure():
    out = receipts.evaluate(
        [_claim("RB depth is the assumption that can break this roster.",
                players=["Josh Jacobs"])],
        rosters={1: {"Someone Else"}}, positional_ranks={}, n_teams=10,
        weeks_played=0, names={1: "Los Bandidos"})
    assert out[0]["status"] == receipts.UNDER_PRESSURE
    assert "Josh Jacobs" in out[0]["status_note"]


def test_a_position_claim_needs_played_weeks_behind_it():
    claim = _claim("RB depth is thin and is the risk here.", positions=["RB"])
    early = receipts.evaluate([claim], rosters={1: set()},
                              positional_ranks={1: {"RB": 10}}, n_teams=10,
                              weeks_played=2, names={1: "x"})
    late = receipts.evaluate([claim], rosters={1: set()},
                             positional_ranks={1: {"RB": 10}}, n_teams=10,
                             weeks_played=6, names={1: "x"})
    assert early[0]["status"] == receipts.TOO_EARLY
    assert late[0]["status"] == receipts.AGING_WELL
    assert "10 of 10" in late[0]["status_note"]


def test_a_worry_that_did_not_happen_reads_as_under_pressure():
    out = receipts.evaluate(
        [_claim("RB depth is thin and can break this roster.", positions=["RB"])],
        rosters={1: set()}, positional_ranks={1: {"RB": 2}}, n_teams=10,
        weeks_played=6, names={1: "x"})
    assert out[0]["status"] == receipts.UNDER_PRESSURE
    assert "holding up" in out[0]["status_note"]


def test_receipts_never_declare_a_take_wrong():
    for status in (receipts.AGING_WELL, receipts.UNDER_PRESSURE, receipts.TOO_EARLY):
        assert "wrong" not in status.lower()
        assert "right" not in status.lower()


def test_team_defenses_do_not_create_receipts_by_surname():
    """"three Bills" is three Buffalo players, not the Buffalo defense."""
    index = receipts._surname_index({"Buffalo Bills": "DEF", "Josh Allen": "QB"})
    assert "Bills" not in index
    assert index["Allen"] == "Josh Allen"


def test_claim_extraction_skips_markdown_tables():
    snap = {"issue_key": "draft", "issue_label": "Draft Issue",
            "href": "2026/draft/index.html",
            "sections": [{"module_key": "custom", "title": "Rankings",
                          "content_md": "| Rk | Team | Score |\n"
                                        "| 1 | Los Bandidos | 100 |\n"
                                        "RB depth is the assumption that could "
                                        "break Los Bandidos this season.\n"}]}
    claims = receipts.extract_claims([snap], "disco",
                                     {1: {"los", "bandidos"}, 2: {"dave"}},
                                     {"A Player": "RB"})
    assert len(claims) == 1
    assert "|" not in claims[0]["quote"]
