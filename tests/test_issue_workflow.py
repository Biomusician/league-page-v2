from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
import leaguepage.publish as pub
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import assemble_issue, module_states
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER
from leaguepage.storage import Storage
from leaguepage.weekly_signals import black_box_events, force_flow_candidates, tracks_and_fades

from fixtures import TEST_LEAGUE, add_players, populate_league, populate_matchups


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Desk + builder sandboxed onto a tmp DB and tmp editorial/site dirs.
    Uses the real 'surfeit' (10-team) league slug so desk routes resolve."""
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(pub, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    league = get_league("surfeit")
    with Storage(db) as s:
        populate_league(s, league, teams=10, rounds=3, picks="complete")
        populate_matchups(s, league, week=1, teams=10,
                          scores={rid: 100.0 + rid for rid in range(1, 11)})
    client = TestClient(create_app(db))
    return client, db, league, tmp_path


def test_workspace_states_and_build(env):
    client, db, league, tmp = env
    r = client.get("/commissioner/surfeit/2026/issue/week-01")
    assert r.status_code == 200
    for stage in ("DATA", "STORIES", "MATCHUPS", "AWARDS", "LOWDOWN", "ISSUE", "PUBLISH"):
        assert stage in r.text
    client.post("/commissioner/surfeit/2026/issue/week-01/build")
    idir = tmp / "editorial" / "2026" / "surfeit" / "week-01"
    assert (idir / "lowdown" / "PREP.md").exists()
    assert (idir / "lowdown" / "AUTHORING.md").exists()
    assert (idir / "AUTHORING_INDEX.md").exists()
    # authoritative skill path in every brief
    assert ".claude/skills/my-writing-style/SKILL.md" in (idir / "lowdown" / "AUTHORING.md").read_text(encoding="utf-8")


def test_story_selection_and_route_persist(env):
    client, db, league, _ = env
    client.post("/commissioner/surfeit/2026/issue/week-01/stories",
                data={"candidate_id": "story:high-score:1", "decision": "include",
                      "route": "lowdown", "note": "open with this"})
    with Storage(db) as s:
        d = s.get_story_decisions("surfeit", "2026", "week-01")["story:high-score:1"]
    assert d["decision"] == "include" and d["route"] == "lowdown" and d["note"] == "open with this"


def test_award_decision_and_custom_award(env):
    client, db, league, _ = env
    client.post("/commissioner/surfeit/2026/issue/week-01/awards",
                data={"award_key": "hard-luck-bastard", "decision": "awarded",
                      "winner": "team-9", "note": ""})
    client.post("/commissioner/surfeit/2026/issue/week-01/awards",
                data={"award_key": "commissioners-special", "decision": "manual",
                      "winner": "team-3", "note": "a made-up award, on purpose"})
    with Storage(db) as s:
        d = s.get_award_decisions("surfeit", "2026", "week-01")
    assert d["hard-luck-bastard"]["winner"] == "team-9"
    assert d["commissioners-special"]["decision"] == "manual"


def test_lowdown_rough_cannot_approve_and_final_can(env):
    client, db, league, tmp = env
    rough = f"<!-- {ROUGH_DRAFT_MARKER} -->\nMachine words."
    client.post("/commissioner/surfeit/2026/issue/week-01/lowdown",
                data={"lowdown_text": rough, "action": "save"})
    client.post("/commissioner/surfeit/2026/issue/week-01/lowdown",
                data={"lowdown_text": "", "action": "approve"})
    with Storage(db) as s:
        mods = s.get_issue_modules("surfeit", "2026", "week-01")
    assert not (mods.get("lowdown") or {}).get("approved")
    # commissioner edit removes the marker; approval then sticks
    client.post("/commissioner/surfeit/2026/issue/week-01/lowdown",
                data={"lowdown_text": "My own words now. Three of them, roughly.",
                      "action": "save"})
    client.post("/commissioner/surfeit/2026/issue/week-01/lowdown",
                data={"lowdown_text": "", "action": "approve"})
    with Storage(db) as s:
        mods = s.get_issue_modules("surfeit", "2026", "week-01")
    assert mods["lowdown"]["approved"] == 1


def test_module_ordering_omission_and_retitle(env):
    client, db, league, _ = env
    client.post("/commissioner/surfeit/2026/issue/week-01/builder/module",
                data={"module_key": "fades", "action": "exclude"})
    client.post("/commissioner/surfeit/2026/issue/week-01/builder/module",
                data={"module_key": "blackbox", "action": "move", "position": "1"})
    client.post("/commissioner/surfeit/2026/issue/week-01/builder/module",
                data={"module_key": "custom", "action": "retitle",
                      "custom_title": "Side Bet Status"})
    with Storage(db) as s:
        modules = module_states(s, league, "2026", "week-01", week=1)
    by_key = {m["module_key"]: m for m in modules}
    assert by_key["fades"]["included"] is False
    assert modules[1]["module_key"] == "blackbox"  # position 1, behind pos-0 masthead
    assert by_key["custom"]["title"] == "Side Bet Status"


def test_intel_module_omits_itself_early(env):
    client, db, league, _ = env
    with Storage(db) as s:
        modules = module_states(s, league, "2026", "week-01", week=1)
        assembled = assemble_issue(s, league, "2026", "week-01", week=1)
    intel = next(m for m in modules if m["module_key"] == "intel")
    assert intel["status"] == "not_ready" and "fake" in intel["detail"] or "precision" in intel["detail"]
    assert all(s_["module_key"] != "intel" for s_ in assembled["sections"])


def test_publish_gates_and_full_publish(env):
    client, db, league, tmp = env
    idir = tmp / "editorial" / "2026" / "surfeit" / "week-01"
    # keep the issue minimal: only masthead + lowdown included
    for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                "blackbox", "false-assumptions", "branches"):
        client.post("/commissioner/surfeit/2026/issue/week-01/builder/module",
                    data={"module_key": key, "action": "exclude"})
    # rough lowdown present -> publish blocked
    (idir / "lowdown").mkdir(parents=True, exist_ok=True)
    (idir / "lowdown" / "lowdown.md").write_text(
        f"<!-- {ROUGH_DRAFT_MARKER} -->\nNot ready.", encoding="utf-8")
    with Storage(db) as s:
        with pytest.raises(pub.PublishError, match="marker|approved"):
            pub.publish_assembled_issue(s, league, "2026", "week-01", week=1)
    # clean + approved -> publishes; site contains the words and the credit
    (idir / "lowdown" / "lowdown.md").write_text(
        "# The Lowdown\n\nWeek one happened. Three things follow from that.\n\n"
        "<!-- usage: frame=competitive -->\n",
        encoding="utf-8")
    client.post("/commissioner/surfeit/2026/issue/week-01/lowdown",
                data={"lowdown_text": "", "action": "approve"})
    with Storage(db) as s:
        out = pub.publish_assembled_issue(s, league, "2026", "week-01", week=1)
        issue = s.get_issue("surfeit", "2026", "week-01")
    html = out.read_text(encoding="utf-8")
    assert "Week one happened" in html
    assert "by the Commissioner" in html
    assert ROUGH_DRAFT_MARKER not in html and "TEST DRAFT" not in html
    assert "<!--" not in html.split("</header>")[1]  # no editorial comments in body
    assert issue["status"] == "published"
    # league home lists the published issue
    home = (tmp / "site" / "surfeit" / "index.html").read_text(encoding="utf-8")
    assert "week-01" in home


def test_unresolved_public_name_blocks_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    league = get_league("surfeit")
    with Storage(db) as s:
        populate_league(s, league, teams=10, rounds=3, picks="complete")
        # strip roster 4's team name so it has no safe public identity
        users = s.get_league_users(league.league_id)
        for u in users:
            if u["user_id"] == "u4":
                u["metadata"] = {}
        s.save_league_users(league.league_id, users)
        populate_matchups(s, league, week=1, teams=10,
                          scores={rid: 100.0 for rid in range(1, 11)})
        with pytest.raises(pub.PublishError, match="Roster 4 has no confirmed public display name"):
            pub.render_week(s, league, 1, site_dir=tmp_path / "site",
                            editorial_dir=tmp_path / "editorial")
        # commissioner confirms a name on the Desk model -> unblocked
        s.set_public_team_name("surfeit", 4, "The Fighting Fours")
        out = pub.render_week(s, league, 1, site_dir=tmp_path / "site",
                              editorial_dir=tmp_path / "editorial")
    assert "The Fighting Fours" in out.read_text(encoding="utf-8")


def test_false_assumptions_desk_flow(env):
    client, db, league, _ = env
    with Storage(db) as s:
        take_id = s.add_take(league_slug="surfeit", season="2026", week=None,
                             context="draft", source="draft-review", subject="team-2",
                             quote="Safest WR room in the league.")
    client.post(f"/commissioner/surfeit/2026/false-assumptions/{take_id}",
                data={"action": "too_early", "resolution": ""})
    with Storage(db) as s:
        take = s.all_takes("surfeit", "2026")[0]
    assert take["status"] == "too_early"
    assert take["quote"] == "Safest WR room in the league."  # never rewritten
    client.post(f"/commissioner/surfeit/2026/false-assumptions/{take_id}",
                data={"action": "use", "issue_key": "week-01", "resolution": ""})
    with Storage(db) as s:
        d = s.get_story_decisions("surfeit", "2026", "week-01")[f"story:take:{take_id}"]
    assert d["route"] == "false-assumptions"


def test_twelve_team_issue_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t12.sqlite3"
    league = get_league("disco")
    with Storage(db) as s:
        populate_league(s, league, teams=12, rounds=3, picks="complete")
        populate_matchups(s, league, week=1, teams=12,
                          scores={rid: 90.0 + rid for rid in range(1, 13)})
    client = TestClient(create_app(db))
    r = client.post("/commissioner/disco/2026/issue/week-01/build", follow_redirects=True)
    assert r.status_code == 200
    with Storage(db) as s:
        modules = module_states(s, league, "2026", "week-01", week=1)
    keys = {m["module_key"] for m in modules}
    # surfeit-only modules stay out of Disco's registry
    assert "branches" not in keys and "false-assumptions" not in keys
    ctp = next(m for m in modules if m["module_key"] == "ctp")
    assert "0/6" in ctp["detail"]  # six matchups in a 12-team league


# ---------------------------------------------------------------- signals

def test_force_flow_significance_filter(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    add_players(storage, {"BIGNAME": ("Big Name", "RB", 40),
                          "NOBODY": ("Practice Squad Guy", "WR", 5000)})
    populate_matchups(storage, week=1, teams=10, scores={rid: 100.0 for rid in range(1, 11)})
    storage.save_transactions(TEST_LEAGUE.league_id, 1, [
        {"transaction_id": "keep", "type": "trade", "status": "complete", "leg": 1,
         "adds": {"BIGNAME": 2}, "drops": {}, "waiver_budget": []},
        {"transaction_id": "drop-big", "type": "free_agent", "status": "complete", "leg": 1,
         "adds": {}, "drops": {"BIGNAME": 3}, "waiver_budget": []},
        {"transaction_id": "noise", "type": "free_agent", "status": "complete", "leg": 1,
         "adds": {"NOBODY": 4}, "drops": {}, "waiver_budget": []},
    ])
    slugs = {rid: f"team-{rid}" for rid in range(1, 11)}
    cands = force_flow_candidates(storage, TEST_LEAGUE, 1, slugs)
    ids = {c["candidate_id"] for c in cands}
    assert "forceflow:keep" in ids
    assert "forceflow:drop-big" in ids
    assert "forceflow:noise" not in ids  # insignificant moves never surface


def test_black_box_detection_and_empty_state(storage):
    from leaguepage.matchup_analysis import analyze_week

    populate_league(storage, teams=10, rounds=3, picks="complete")
    for wk in range(1, 3):
        populate_matchups(storage, week=wk, teams=10,
                          scores={rid: 100.0 + rid for rid in range(1, 11)})
    a = analyze_week(storage, TEST_LEAGUE, 2)
    assert a is not None
    assert black_box_events(storage, TEST_LEAGUE, 2, a) == []  # < 3 weeks: no records
    populate_matchups(storage, week=3, teams=10,
                      scores={**{rid: 100.0 + rid for rid in range(1, 11)}, 5: 199.0})
    a = analyze_week(storage, TEST_LEAGUE, 3)
    events = black_box_events(storage, TEST_LEAGUE, 3, a)
    high = next(e for e in events if "season-high" in e["candidate_id"])
    assert "team-5" in high["teams"][0]
    assert "weeks 1-3" in high["confidence"]


def test_tracks_and_fades_selection(storage):
    from leaguepage.matchup_analysis import analyze_week

    populate_league(storage, teams=10, rounds=3, picks="complete")
    # team 2 scores huge every week but loses each time to team 1 (paired 1v2):
    # strong all-play, bad record -> Track. team 9 wins vs team 10 with weak
    # scores -> record outruns all-play -> Fade.
    for wk in range(1, 4):
        scores = {rid: 80.0 + rid for rid in range(1, 11)}
        scores[1], scores[2] = 160.0, 150.0
        scores[9], scores[10] = 89.5, 60.0
        populate_matchups(storage, week=wk, teams=10, scores=scores)
    populate_matchups(storage, week=4, teams=10)  # upcoming, unplayed
    from fixtures import set_records
    set_records(storage, records={1: (3, 0, 480), 2: (0, 3, 450), 9: (3, 0, 268), 10: (0, 3, 180)})
    a = analyze_week(storage, TEST_LEAGUE, 4)
    tracks, fades = tracks_and_fades(storage, TEST_LEAGUE, 4, a)
    assert any(c["teams"] == ["team-2"] for c in tracks)
    assert any(c["teams"] == ["team-9"] for c in fades)
    assert all(any("window" in f or "small sample" in f for f in c["facts"])
               for c in tracks + fades)
