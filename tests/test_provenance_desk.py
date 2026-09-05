"""Provenance is assigned by the Desk's workflow, never by hand.

Every transition in the model, driven through the real endpoints:
a Claude draft arriving under the ROUGH DRAFT contract, writing into an
empty section, a proposal accepted or discarded, an exact restore, the one
deliberate act that changes origin, the Lowdown's rough draft, matchup
previews under the human-origin workflow, ranking notes, and what a
published snapshot carries.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import provenance as pv
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"
MARK = f"<!-- {ROUGH_DRAFT_MARKER} -->\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    idir = ed / SEASON / "surfeit" / "week-01"
    for sub in ("lowdown", "sections", "proposals"):
        (idir / sub).mkdir(parents=True)
    return TestClient(create_app(db_path=db)), db, idir


def _save(client, section, text):
    r = client.post(f"{EDIT}/save", json={"section": section, "text": text, "base_sha": ""})
    assert r.status_code == 200, r.text
    return r


def _state(db, section, text):
    with Storage(db) as s:
        return pv.state_for(s, league_slug="surfeit", season=SEASON, issue_key="week-01",
                            section=section, text=text)


def _label(db, section, text):
    st = _state(db, section, text)
    return st["label"] if st else None


def _child(db):
    with Storage(db) as s:
        return ib.matchup_children(s, LG, SEASON, "week-01", week=1)[0]


# ------------------------------------------------------------ arrival

def test_a_claude_draft_under_the_marker_is_ai_in_origin(env):
    """The marker is the authoring contract. Removing it is not an edit;
    the first real change is, and the label says so while the origin holds."""
    client, db, idir = env
    (idir / "sections" / "tracks.md").write_text(MARK + "Generated words.\n", encoding="utf-8")
    _save(client, "tracks", "Generated words.\n")
    assert _label(db, "tracks", "Generated words.\n") == "AI-generated"
    assert "exact generated baseline" in client.get(EDIT).text
    _save(client, "tracks", "Generated words, and a few of mine.\n")
    assert _label(db, "tracks", "Generated words, and a few of mine.\n") == "AI-generated · Commish edited"
    assert "changed from generated baseline" in client.get(EDIT).text


def test_text_of_unknown_origin_stays_unlabelled_when_edited(env):
    """An edit to a text nobody recorded proves nothing about the rest."""
    client, db, idir = env
    (idir / "sections" / "fades.md").write_text("Words of no known author.\n", encoding="utf-8")
    _save(client, "fades", "Words of no known author, edited.\n")
    assert _state(db, "fades", "Words of no known author, edited.\n") is None
    assert "Origin not recorded" in client.get(EDIT).text


# ------------------------------------------------------------ his own writing

def test_writing_into_an_empty_section_is_commissioner_written(env):
    client, db, idir = env
    _save(client, "fades", "Mine, from nothing.\n")
    assert _label(db, "fades", "Mine, from nothing.\n") == "Commish-written"
    # AI help arrives beside the box: the next save records the assistance
    (idir / "proposals" / "fades.md").write_text(MARK + "A suggestion.\n", encoding="utf-8")
    _save(client, "fades", "Mine, from nothing, and more.\n")
    assert _label(db, "fades", "Mine, from nothing, and more.\n") == "Commish-written · AI-assisted"


def test_discarding_a_proposal_records_assistance_and_keeps_his_text(env):
    client, db, idir = env
    _save(client, "fades", "Mine.\n")
    (idir / "proposals" / "fades.md").write_text(MARK + "A suggestion.\n", encoding="utf-8")
    r = client.post(f"{EDIT}/proposal", json={"section": "fades", "action": "discard"})
    assert r.status_code == 200
    assert (idir / "sections" / "fades.md").read_text(encoding="utf-8") == "Mine.\n"
    assert _label(db, "fades", "Mine.\n") == "Commish-written · AI-assisted"


def test_a_discarded_proposal_on_unknown_text_labels_nothing(env):
    """Assistance is noted; origin is still unknown, so no label."""
    client, db, idir = env
    (idir / "sections" / "fades.md").write_text("Old words.\n", encoding="utf-8")
    (idir / "proposals" / "fades.md").write_text(MARK + "A suggestion.\n", encoding="utf-8")
    client.post(f"{EDIT}/proposal", json={"section": "fades", "action": "discard"})
    assert _state(db, "fades", "Old words.\n") is None
    assert "AI assistance noted" in client.get(EDIT).text


# ------------------------------------------------------------ accept, edit, restore

def test_accept_then_edit_then_exact_restore(env):
    client, db, idir = env
    (idir / "proposals" / "tracks.md").write_text(MARK + "Proposed.\n", encoding="utf-8")
    r = client.post(f"{EDIT}/proposal", json={"section": "tracks", "action": "accept"})
    assert r.status_code == 200, r.text
    assert _label(db, "tracks", "Proposed.\n") == "AI-generated"
    _save(client, "tracks", "Proposed, and then some.\n")
    assert _label(db, "tracks", "Proposed, and then some.\n") == "AI-generated · Commish edited"
    revs = client.get(f"{EDIT}/revisions", params={"section": "tracks"}).json()["revisions"]
    exact = next(rv for rv in revs if rv["source"] == "commissioner-save")
    client.post(f"{EDIT}/restore", json={"section": "tracks", "revision_id": exact["id"]})
    assert (idir / "sections" / "tracks.md").read_text(encoding="utf-8").strip() == "Proposed."
    assert _label(db, "tracks", "Proposed.\n") == "AI-generated"
    assert "exact generated baseline" in client.get(EDIT).text


def test_replace_with_my_copy_is_the_only_way_origin_changes(env):
    client, db, idir = env
    (idir / "proposals" / "tracks.md").write_text(MARK + "Proposed words here.\n", encoding="utf-8")
    client.post(f"{EDIT}/proposal", json={"section": "tracks", "action": "accept"})
    rewrite = "Entirely different words, every one of them, and more of them too.\n"
    _save(client, "tracks", rewrite)
    assert _label(db, "tracks", rewrite) == "AI-generated · Commish edited", \
        "a total rewrite is still AI in origin"
    r = client.post(f"{EDIT}/replace-origin", json={"section": "tracks"})
    assert r.status_code == 400, "confirmation required"
    r = client.post(f"{EDIT}/replace-origin", json={"section": "tracks", "confirm": "yes"})
    assert r.status_code == 200, r.text
    assert (idir / "sections" / "tracks.md").read_text(encoding="utf-8") == ""
    revs = client.get(f"{EDIT}/revisions", params={"section": "tracks"}).json()["revisions"]
    assert revs[0]["source"] == "replace-with-my-copy" and "different words" in revs[0]["preview"]
    _save(client, "tracks", "Mine now.\n")
    assert _label(db, "tracks", "Mine now.\n") == "Commish-written · AI-assisted"
    r = client.post(f"{EDIT}/replace-origin", json={"section": "tracks", "confirm": "yes"})
    assert r.status_code == 400, "already his; nothing to replace"


# ------------------------------------------------------------ the Lowdown

def test_reset_to_the_lowdown_rough_draft_is_ai_origin(env):
    client, db, idir = env
    (idir / "lowdown" / "rough-lowdown.md").write_text(MARK + "Rough lowdown.\n", encoding="utf-8")
    r = client.post(f"{EDIT}/reset-generated", json={"section": "lowdown", "confirm": "yes"})
    assert r.status_code == 200, r.text
    assert _label(db, "lowdown", "Rough lowdown.\n") == "AI-generated"
    _save(client, "lowdown", "Rough lowdown, sharpened.\n")
    assert _label(db, "lowdown", "Rough lowdown, sharpened.\n") == "AI-generated · Commish edited"


def test_a_rough_file_without_the_marker_earns_no_claim(env):
    client, db, idir = env
    (idir / "lowdown" / "rough-lowdown.md").write_text("Who knows who wrote this.\n", encoding="utf-8")
    client.post(f"{EDIT}/reset-generated", json={"section": "lowdown", "confirm": "yes"})
    assert _state(db, "lowdown", "Who knows who wrote this.\n") is None


def test_lowdown_written_beside_a_rough_draft_is_commissioner_ai_assisted(env):
    client, db, idir = env
    (idir / "lowdown" / "rough-lowdown.md").write_text(MARK + "Rough lowdown.\n", encoding="utf-8")
    _save(client, "lowdown", "My own lowdown.\n")
    assert _label(db, "lowdown", "My own lowdown.\n") == "Commish-written · AI-assisted"


def test_lowdown_written_alone_is_commissioner_written(env):
    client, db, idir = env
    _save(client, "lowdown", "My own lowdown.\n")
    assert _label(db, "lowdown", "My own lowdown.\n") == "Commish-written"


# ------------------------------------------------------------ matchups

def test_a_matchup_preview_he_writes_beside_a_suggestion_is_his(env):
    client, db, idir = env
    child = _child(db)
    (idir / "proposals" / f"matchup--{child['slug']}.md").write_text(
        MARK + "Suggested preview.\n", encoding="utf-8")
    _save(client, child["section"], "My preview.\n")
    assert _label(db, child["section"], "My preview.\n") == "Commish-written · AI-assisted"
    client.post(f"{EDIT}/approve", json={"section": child["section"], "action": "approve"})
    with Storage(db) as s:
        body = next(x["content_md"] for x in
                    ib.assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                    if x["module_key"] == "ctp")
        assert "Commish-written · AI-assisted" in body
        assert body.index("###") < body.index("Commish-written") < body.index("My preview.")
        assert pv.section_state(s, league_slug="surfeit", season=SEASON, issue_key="week-01",
                                section="ctp", text=body) is None
        # the inline line is markup inside prose; the publication check must
        # not mistake it for a formatting or privacy problem
        from leaguepage import pubqa

        rep = pubqa.report(pubqa.check_sections(
            [{"module_key": "ctp", "title": "Common Tactical Picture", "content_md": body}],
            pubqa.build_context(s, LG, SEASON, "week-01", week=1)))
        assert not rep["blockers"], rep["blockers"]


def test_accepting_a_matchup_proposal_is_ai_origin_and_says_so(env):
    client, db, idir = env
    child = _child(db)
    (idir / "proposals" / f"matchup--{child['slug']}.md").write_text(
        MARK + "Suggested preview.\n", encoding="utf-8")
    r = client.post(f"{EDIT}/proposal", json={"section": child["section"], "action": "accept"})
    assert r.status_code == 200, r.text
    assert _label(db, child["section"], "Suggested preview.\n") == "AI-generated"
    _save(client, child["section"], "Suggested preview, sharpened.\n")
    assert _label(db, child["section"], "Suggested preview, sharpened.\n") == "AI-generated · Commish edited"


def test_the_matchup_authoring_contract_proposes_and_never_writes_the_draft():
    src = pathlib.Path("leaguepage/matchup_packet.py").read_text(encoding="utf-8")
    assert "Write `../draft.md`" not in src
    assert "proposals/matchup--" in src
    assert "Commissioner-written" in src


# ------------------------------------------------------------ rankings

def test_ranking_notes_make_peer_and_near_peer_commissioner_written(env):
    client, db, _idir = env
    r = client.post(f"/commissioner/surfeit/{SEASON}/draft-review/power",
                    data={"rank_1": "1", "tier_1": "1", "note_1": "Best roster on paper"},
                    follow_redirects=False)
    assert r.status_code == 303
    with Storage(db) as s:
        st = pv.state_for(s, league_slug="surfeit", season=SEASON, issue_key="draft",
                          section="power", text="whatever the module assembles")
    assert st["label"] == "Commish-written"


def test_a_ranking_without_notes_claims_no_prose(env):
    client, db, _idir = env
    client.post(f"/commissioner/surfeit/{SEASON}/draft-review/power",
                data={"rank_1": "1", "tier_1": "1"}, follow_redirects=False)
    with Storage(db) as s:
        assert s.get_prose_provenance("surfeit", SEASON, "draft", "power") is None


# ------------------------------------------------------------ publication

def test_a_snapshot_carries_labels_and_never_a_baseline(env):
    from leaguepage.publish import publish_assembled_issue

    client, db, idir = env
    (idir / "proposals" / "tracks.md").write_text(MARK + "Proposed tracks.\n", encoding="utf-8")
    client.post(f"{EDIT}/proposal", json={"section": "tracks", "action": "accept"})
    _save(client, "lowdown", "My lowdown.\n")
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "fades", "forceflow", "blackbox",
                    "false-assumptions", "branches", "draft-capsules", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=0)
        for key in ("lowdown", "tracks"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, approved=1)
        path = publish_assembled_issue(s, LG, SEASON, "week-01", week=1)
    import json

    snap = json.loads(path.read_text(encoding="utf-8"))
    provs = {sec["module_key"]: sec["provenance"] for sec in snap["sections"]}
    assert provs["tracks"]["label"] == "AI-generated"
    assert provs["lowdown"]["label"] == "Commish-written"
    raw = path.read_text(encoding="utf-8")
    for forbidden in ("baseline_text", "generated_sha", "Proposed tracks.\\n\"", "changed_from"):
        assert forbidden not in raw or forbidden == "Proposed tracks.\\n\"", forbidden


# ------------------------------------------------------------ retroactive

def test_backfill_records_only_marker_proven_sections(tmp_path):
    import sys

    sys.path.insert(0, "scripts")
    from backfill_provenance import backfill, candidates

    with Storage(tmp_path / "t.sqlite3") as s:
        s.add_prose_revision("surfeit", SEASON, "week-01", "tracks",
                             MARK + "Claude wrote this first.\n", "provenance-migration")
        s.add_prose_revision("surfeit", SEASON, "week-01", "tracks",
                             "Claude wrote this first, edited.\n", "commissioner-save")
        s.add_prose_revision("surfeit", SEASON, "week-01", "fades",
                             "Nobody knows who wrote this.\n", "commissioner-save")
        assert [c["section"] for c in candidates(s)] == ["tracks"]
        lines = backfill(s, apply=True)
        assert len(lines) == 1 and "Claude wrote" not in lines[0], "no prose in the report"
        row = s.get_prose_provenance("surfeit", SEASON, "week-01", "tracks")
        assert row["origin"] == "ai" and row["event"] == "backfill"
        assert pv.classify(row, "Claude wrote this first, edited.\n")["label"] \
            == "AI-generated · Commish edited"
        assert s.get_prose_provenance("surfeit", SEASON, "week-01", "fades") is None
        assert backfill(s, apply=True) == [], "idempotent"
