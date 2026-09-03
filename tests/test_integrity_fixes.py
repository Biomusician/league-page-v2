"""Defects that shipped confident nonsense rather than an error."""
from __future__ import annotations

from leaguepage.config import get_league
import pytest

from leaguepage.matchup_analysis import analyze_week
from leaguepage.publish import PublishError, publish_assembled_issue
from leaguepage.storage import Storage
from leaguepage.receipts import evaluate, extract_claims
from leaguepage.takes import VOID, _matchup_evidence
from leaguepage.team_analytics import team_outlook

from fixtures import add_players, populate_league, populate_matchups
from season import populate_season

DISCO = get_league("disco")
SURFEIT = get_league("surfeit")
SEASON = "2027"


# ------------------------------------------------------------- ties

def test_a_drawn_game_is_not_a_loss_for_both_teams(storage):
    populate_league(storage, DISCO, teams=12, season=SEASON)
    populate_matchups(storage, DISCO, week=1, teams=12,
                      scores={rid: (100.0 if rid in (1, 2) else 90.0 + rid)
                              for rid in range(1, 13)})
    populate_matchups(storage, DISCO, week=2, teams=12,
                      scores={rid: 95.0 + rid for rid in range(1, 13)})
    analysis = analyze_week(storage, DISCO, 2)
    by_rid = {t["roster_id"]: t for t in analysis["teams"].values()}
    assert by_rid[1]["streak"] == "T1"
    assert by_rid[2]["streak"] == "T1"
    assert by_rid[3]["streak"] in ("W1", "L1")


def test_a_tied_matchup_take_is_void_not_busted():
    """Calling a drawn game BUSTED and quoting him on the front page for it
    is the kind of confident nonsense that discredits the whole feature."""
    ctx = {"matchup_results": {1: {"week": 3, "won": False, "tie": True,
                                   "line": "Alpha 100.0 - 100.0 Bravo"}}}
    take = {"subject_roster_id": 1, "quote": "Alpha will win this one comfortably."}
    status, lines = _matchup_evidence(ctx, take)
    assert status == VOID
    assert any("tie" in line for line in lines)


def test_a_decided_matchup_take_still_resolves():
    ctx = {"matchup_results": {1: {"week": 3, "won": True, "tie": False,
                                   "line": "Alpha 120.0 - 100.0 Bravo"}}}
    take = {"subject_roster_id": 1, "quote": "Alpha will win this one comfortably."}
    status, _ = _matchup_evidence(ctx, take)
    assert status == "resolved_right"


# ------------------------------------------ special teams, everywhere

def test_team_outlook_does_not_define_a_team_by_its_kicker(storage):
    """This renders as 'defining this team right now', two lines under a
    paragraph promising skill-position construction."""
    populate_season(storage, SURFEIT, teams=10, weeks_played=0, season=SEASON)
    add_players(storage, {f"p{i}": (f"Player Number{i}", "K" if i % 3 == 0 else "WR",
                                    100 + i) for i in range(1, 40)})
    for rid in range(1, 11):
        signals = team_outlook(storage, SURFEIT, SEASON, rid, 0)
        for s in signals:
            assert not s.startswith("K "), (rid, signals)
            assert not s.startswith("DEF "), (rid, signals)


def test_a_kicker_premium_claim_never_becomes_a_receipt():
    """One ordinary kicker drop could otherwise put the exact sentence a
    published correction was issued to de-emphasise back on the front page
    at full weight."""
    snaps = [{
        "issue_key": "draft", "issue_label": "Draft Issue",
        "href": "2026/draft/index.html",
        "sections": [{"title": "Rankings", "content_md":
                      "### 1. Bandit Country\n\n"
                      "Jason Myers 95 picks early is the single largest kicker "
                      "premium in the league, and that is a real risk.\n\n"
                      "Bijan Robinson at that price is a concentrated bet and a "
                      "genuine risk on one offense.\n"}],
    }]
    tokens = {1: {"bandit", "country"}}
    positions = {"Jason Myers": "K", "Bijan Robinson": "RB"}
    claims = extract_claims(snaps, "surfeit", tokens, positions)
    quotes = " ".join(c["quote"] for c in claims)
    assert "Bijan Robinson" in quotes
    assert "Jason Myers" not in quotes, "the kicker premium survived extraction"


def test_a_dropped_kicker_cannot_put_a_claim_under_pressure():
    """Special-teams players are recorded because the claim named them, but
    only skill players can carry a verdict."""
    claim = {"claim_id": "c1", "roster_id": 1,
             "quote": "Bijan Robinson and Jason Myers carry real risk here.",
             "positions": ["RB"],
             "players": ["Bijan Robinson", "Jason Myers"],
             "judgeable": ["Bijan Robinson"],
             "issue_key": "draft", "issue_label": "Draft Issue",
             "href": "x.html", "section_title": "Rankings"}
    out = evaluate([claim], rosters={1: {"Bijan Robinson"}},
                   positional_ranks={1: {"RB": 2}}, n_teams=10,
                   weeks_played=4, names={1: "Bandit Country"})
    assert out
    assert "Jason Myers" not in (out[0].get("note") or ""), out[0]


# ----------------------------------------------------- the frozen record

def _publish_env(tmp_path, monkeypatch, text):
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, DISCO, teams=12, season=SEASON)
        for k in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                  "blackbox", "false-assumptions", "branches", "draft-capsules"):
            s.set_issue_module(league_slug="disco", season=SEASON, issue_key="draft",
                               module_key=k, included=0)
        d = tmp_path / "editorial" / SEASON / "disco" / "draft" / "lowdown"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lowdown.md").write_text(text, encoding="utf-8")
        s.set_issue_module(league_slug="disco", season=SEASON, issue_key="draft",
                           module_key="lowdown", approved=1)
    return db


def _publish(db, tmp_path):
    with Storage(db) as s:
        return publish_assembled_issue(s, DISCO, SEASON, "draft",
                                       published_dir=tmp_path / "published",
                                       base_dir=tmp_path / "editorial")


def test_an_identical_republish_is_a_no_op(tmp_path, monkeypatch):
    """A deploy that fails after the snapshot stage gets retried, and the
    retry must not be an error."""
    db = _publish_env(tmp_path, monkeypatch, "The room was set, and then it was not.")
    first = _publish(db, tmp_path)
    again = _publish(db, tmp_path)
    assert again == first
    assert sorted(p.name for p in first.parent.iterdir()) == ["draft.json"]


def test_a_changed_republish_cannot_rewrite_what_shipped(tmp_path, monkeypatch):
    """The whole promise of published/ is that the record of what shipped is
    still on disk. This overwrote it in place, with no revision and no
    'Updated' line."""
    original = "The room was set, and then it was not."
    db = _publish_env(tmp_path, monkeypatch, original)
    path = _publish(db, tmp_path)
    (tmp_path / "editorial" / SEASON / "disco" / "draft" / "lowdown" / "lowdown.md"
     ).write_text("Completely different words nobody reviewed.", encoding="utf-8")
    with pytest.raises(PublishError, match="rewrite the record"):
        _publish(db, tmp_path)
    assert original in path.read_text(encoding="utf-8")


# ------------------------------------------------------- season rollover

def test_last_seasons_claims_do_not_resolve_against_this_years_rosters(storage):
    """Every player moves between seasons, so on the first sync of a new
    year every claim from the old one fires at once."""
    from leaguepage.receipts import live_receipts
    populate_league(storage, DISCO, teams=12, season="2027")
    names = {rid: {"name": f"Team {rid}"} for rid in range(1, 13)}
    old = [{"season": "2026", "issue_key": "draft", "issue_label": "Draft Issue",
            "href": "2026/draft/index.html",
            "sections": [{"title": "Rankings", "content_md":
                          "### 1. Team 1\n\nBijan Robinson is a real risk at that "
                          "price and the whole thing depends on him.\n"}]}]
    assert live_receipts(storage, DISCO, "2027", 1, old, names) == []


# ------------------------------------------------- rosters with nobody in them

def test_an_empty_roster_is_not_a_strength(storage):
    """Empty rooms all tie at zero and the stable sort hands the lowest
    roster_id the top spot, so an abandoned team published 'TE room ranks
    2/4 with real depth behind the starters'."""
    from leaguepage.team_analytics import positional_profile, strengths_weaknesses
    populate_league(storage, DISCO, teams=12, season=SEASON)
    rosters = storage.get_rosters(DISCO.league_id)
    for r in rosters:
        r["players"] = [] if r["roster_id"] == 1 else ["p1", "p2"]
    storage.save_rosters(DISCO.league_id, rosters)
    add_players(storage, {"p1": ("A Back", "RB", 5), "p2": ("A End", "TE", 9)})
    profile = positional_profile(storage, DISCO)
    sw = strengths_weaknesses(profile, 1)
    assert sw["strengths"] == [], sw["strengths"]


def test_an_unpaired_week_is_not_a_played_week(storage):
    """analyze_week and the points-against tally both drop rows with no
    matchup_id. weekly_scores did not, so the standings claimed a week the
    Common Tactical Picture rendered as empty."""
    from leaguepage.matchup_analysis import weekly_scores
    populate_league(storage, DISCO, teams=12, season=SEASON)
    populate_matchups(storage, DISCO, week=1, teams=12,
                      scores={rid: 100.0 + rid for rid in range(1, 13)})
    rows = storage.get_matchups(DISCO.league_id, 1)
    for r in rows:
        r["matchup_id"] = None
    storage.save_matchups(DISCO.league_id, 1, rows)
    assert weekly_scores(storage, DISCO.league_id, 1) == {}
