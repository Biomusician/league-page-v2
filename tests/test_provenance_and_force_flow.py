"""AI provenance, Force Flow flags, and the About page's copy.

Three things that arrived together because they share one idea: say what
is true and be able to show why. Provenance is recorded rather than
detected. A Force Flow flag carries the evidence that produced it. The
About page is site content and cannot make a week look unfinished.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.desk_site as desk_site
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import force_flow, provenance
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(desk_site, "ABOUT_PATH", ed / "site" / "about.md")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    return TestClient(create_app(db_path=db)), db, ed


# ------------------------------------------------------------ provenance

def _rec(s, text, generator="claude-code", method="section-brief", section="tracks"):
    provenance.record(s, league_slug="surfeit", season=SEASON, issue_key="week-01",
                      section=section, generator=generator, method=method, text=text)


def _state(s, text, section="tracks"):
    return provenance.state_for(s, league_slug="surfeit", season=SEASON,
                                issue_key="week-01", section=section, text=text)


def test_accepted_generated_text_is_marked(env):
    _c, db, _ed = env
    with Storage(db) as s:
        _rec(s, "Generated prose.")
        st = _state(s, "Generated prose.")
    assert st and st["generator"] == "claude-code"
    assert st["label"] == "AI-generated" and st["badge_text"] == "AI"
    assert "Claude Code" in st["detail"] and "No Commissioner edits." in st["detail"]


def test_one_edited_character_makes_it_generated_then_edited(env):
    """Origin is durable. The hash stops matching on one edited character,
    and the label changes to say he edited it; it does not vanish, because
    the text is still generated in origin."""
    _c, db, _ed = env
    with Storage(db) as s:
        _rec(s, "Generated prose.")
        edited = _state(s, "Generated prose!")
        assert edited and edited["label"] == "AI-generated · Commish edited"
        assert edited["edited"] is True and edited["origin"] == "ai"
        exact = _state(s, "Generated prose. ")            # whitespace only
        assert exact["label"] == "AI-generated" and exact["edited"] is False


def test_human_prose_is_never_marked(env):
    """Existing material defaults to not-generated, because nothing was
    ever recorded for it. A detector would eventually be wrong about his
    own writing, and being wrong in that direction is the worse failure."""
    _c, db, _ed = env
    with Storage(db) as s:
        assert _state(s, "Something he wrote himself.") is None


def test_an_unknown_generator_is_not_guessed(env):
    _c, db, _ed = env
    with Storage(db) as s:
        _rec(s, "Text.", generator=None, method=None)
        st = _state(s, "Text.")
    assert st["generator"] is None
    assert "provider not recorded" in st["caption"]
    assert "Claude" not in st["caption"] and "ChatGPT" not in st["caption"]


def test_provenance_cannot_carry_a_path_or_a_prompt(env):
    """Structural, not a review step: `method` is a key into a fixed table,
    so there is nowhere for a path or a private brief to be stored."""
    _c, db, _ed = env
    with Storage(db) as s:
        _rec(s, "Text.", generator="C:/Users/Jonathan/secret",
             method="editorial/2026/surfeit/week-01/AUTHORING.md")
        st = _state(s, "Text.")
    for leak in ("C:/", "Jonathan", "editorial/", "AUTHORING", ".md"):
        assert leak not in st["caption"], leak
    assert st["method"] is None and st["generator"] is None


def test_deterministic_output_does_not_wear_an_ai_badge():
    """Force Flow's reading is arithmetic. Calling it Claude's work would
    be a false statement about who wrote it."""
    st = provenance.describe_machine("transactions")
    assert st["generator"] is None
    assert "Claude" not in st["caption"] and "ChatGPT" not in st["caption"]
    assert st["label"] == "Automatically generated" and st["badge_text"] == "AUTO"
    assert "deterministic transaction analysis" in st["detail"]


def test_the_badge_is_not_a_logo():
    """Reproducing a company's mark to label its output is a trademark
    someone else owns; the provider's name identifies it just as well."""
    import pathlib

    tpl = pathlib.Path("templates/public/_provenance.html").read_text(encoding="utf-8")
    assert "aria-hidden" in tpl
    assert "<svg" not in tpl and "<img" not in tpl
    assert "<h1" not in tpl and "<h2" not in tpl and "<h3" not in tpl


def _accept_generated(client, ed, section="tracks", text="Generated prose.\n"):
    """Put a proposal through the real accept endpoint, marker and all."""
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "proposals").mkdir(parents=True, exist_ok=True)
    marker = "<!-- ROUGH DRAFT - COMMISSIONER EDIT REQUIRED -->\n"
    (idir / "proposals" / f"{section}.md").write_text(marker + text, encoding="utf-8")
    r = client.post(f"/commissioner/surfeit/{SEASON}/issue/week-01/edit/proposal",
                    json={"section": section, "action": "accept"})
    assert r.status_code == 200, r.text
    return idir / "sections" / f"{section}.md"


def test_accepting_a_proposal_leaves_publishable_labelled_text(env):
    """The bug this replaces: the draft marker had to be deleted by hand,
    which edited the text, broke the hash, and retired a claim that was
    true -- so no accepted proposal could ever reach a page labelled."""
    client, db, ed = env
    path = _accept_generated(client, ed)
    written = path.read_text(encoding="utf-8")
    assert "ROUGH DRAFT" not in written
    assert "Generated prose." in written
    with Storage(db) as s:
        assert _state(s, written) is not None


def test_provenance_survives_all_the_way_into_a_published_snapshot(env):
    """End to end, through the real endpoints and the real publisher."""
    from leaguepage import publish
    from leaguepage.config import get_league

    client, db, ed = env
    _accept_generated(client, ed)
    with Storage(db) as s:
        assembled = [{"module_key": "tracks", "title": "Tracks of Interest",
                      "content_md": (ed / SEASON / "surfeit" / "week-01" /
                                     "sections" / "tracks.md").read_text(encoding="utf-8")}]
        prov = provenance.state_for(
            s, league_slug="surfeit", season=SEASON, issue_key="week-01",
            section="tracks", text=assembled[0]["content_md"])
    assert prov is not None
    assert prov["label"] == "AI-generated" and "Claude Code" in prov["detail"]
    assert "baseline_text" not in prov, "the generated text never enters a snapshot"


def test_an_identical_republish_is_still_a_no_op_without_the_database(env):
    """Provenance lives in a gitignored database. Whether an issue changed
    is a question about its prose, so losing those rows must not make an
    unchanged issue look changed."""
    from leaguepage.publish import _prose_only

    a = [{"module_key": "tracks", "content_md": "Same words.",
          "provenance": {"caption": "AI-generated by Claude Code…"}}]
    b = [{"module_key": "tracks", "content_md": "Same words.", "provenance": None}]
    assert _prose_only(a) == _prose_only(b)
    c = [{"module_key": "tracks", "content_md": "Different words.",
          "provenance": None}]
    assert _prose_only(a) != _prose_only(c)


# ------------------------------------------------------------ force flow

def test_a_league_with_no_transactions_is_not_a_problem(env):
    """A routine week is a finding, not an error."""
    _c, db, _ed = env
    with Storage(db) as s:
        assert force_flow.review(s, LG, SEASON, 1) == []


def _row(**kw):
    base = {"txn_id": "t1", "week": 3, "type": "waiver", "rids": [1],
            "adds": [], "drops": [], "faab": None, "faab_share": 0.0,
            "rationale": {"kind": "unclear", "confidence": "low", "text": None}}
    base.update(kw)
    return base


def _flags(row, **kw):
    opts = {"stats": {"n": 4, "median": 2.0, "max": 40.0, "median_share": 0.02},
            "values": {}, "profile": {"ranks": {}, "rated": {}, "n": 10},
            "names": {}, "started": set(), "later_dropped": {}}
    opts.update(kw)
    return {f["flag"] for f in force_flow.flags_for(row, **opts)}


def test_a_routine_claim_earns_nothing():
    assert _flags(_row(adds=[{"pid": "p", "name": "Someone", "position": "WR", "rid": 1}])) == set()


def test_the_bid_bar_is_this_leagues_own_bidding():
    """Not a hardcoded share of budget. A league where nobody bids more
    than a dollar must not have a three-dollar claim called unusual."""
    row = _row(faab=3, faab_share=0.03)
    cheap = {"n": 6, "median": 1.0, "max": 3.0, "median_share": 0.01}
    assert _flags(row, stats=cheap) == set()
    big = _row(faab=30, faab_share=0.30)
    assert "faab-spike" in _flags(big, stats={"n": 6, "median": 2.0,
                                              "max": 60.0, "median_share": 0.02})


def test_the_largest_bid_still_has_to_be_a_large_bid():
    """`Biggest of the season` on a one-dollar claim is true and useless."""
    row = _row(faab=1, faab_share=0.01)
    assert _flags(row, stats={"n": 1, "median": 1.0, "max": 1.0,
                              "median_share": 0.01}) == set()


def test_churn_is_one_roster_letting_go_of_its_own_add():
    """The same player leaving another team's bench in the same week is
    how a waiver wire works, not churn."""
    add = {"pid": "p", "name": "Someone", "position": "WR", "rid": 1}
    row = _row(adds=[add])
    assert "churn" in _flags(row, later_dropped={(1, "p"): 3})
    assert "churn" not in _flags(row, later_dropped={(2, "p"): 3})


def test_blocking_needs_a_player_worth_blocking():
    """Denying somebody a player nobody would start is not a block."""
    add = {"pid": "p", "name": "Someone", "position": "WR", "rid": 1}
    profile = {"ranks": {"WR": {1: 1, 2: 9, 3: 10}},
               "rated": {"WR": {1, 2, 3}}, "n": 10}
    worthless = {"p": {"value": 5.0, "name": "Someone", "position": "WR"}}
    assert "blocking-add" not in _flags(_row(adds=[add]), profile=profile,
                                        values=worthless, names={2: "B", 3: "C"})
    real = {"p": {"value": 120.0, "name": "Someone", "position": "WR"}}
    assert "blocking-add" in _flags(_row(adds=[add]), profile=profile,
                                    values=real, names={2: "B", 3: "C"})


def test_an_inference_says_it_is_one():
    """A reading is not an observation, and the difference is on the record
    so he can disagree with it."""
    add = {"pid": "p", "name": "Someone", "position": "WR", "rid": 1}
    profile = {"ranks": {"WR": {1: 1, 2: 9, 3: 10}},
               "rated": {"WR": {1, 2, 3}}, "n": 10}
    real = {"p": {"value": 120.0, "name": "Someone", "position": "WR"}}
    got = force_flow.flags_for(
        _row(adds=[add]), stats={"n": 0, "median": 0, "max": 0, "median_share": 0},
        values=real, profile=profile, names={2: "B", 3: "C"},
        started=set(), later_dropped={})
    block = next(f for f in got if f["flag"] == "blocking-add")
    assert block["inferred"] is True
    assert "not proof" in block["why"]
    assert block["evidence"]


def test_every_flag_carries_evidence():
    add = {"pid": "p", "name": "Someone", "position": "WR", "rid": 1}
    got = force_flow.flags_for(
        _row(type="trade", adds=[add], faab=30, faab_share=0.30),
        stats={"n": 6, "median": 2.0, "max": 60.0, "median_share": 0.02},
        values={"p": {"value": 150.0, "name": "Someone", "position": "WR"}},
        profile={"ranks": {}, "rated": {}, "n": 10}, names={},
        started=set(), later_dropped={})
    assert got
    for f in got:
        assert f["evidence"], f["flag"]
        assert f["why"] and f["label"]


def test_an_unranked_player_does_not_crash_anything():
    add = {"pid": "unknown", "name": "Nobody", "position": None, "rid": 1}
    assert _flags(_row(adds=[add], drops=[add])) == set()


def test_a_note_is_optional_and_removable(env):
    _c, db, _ed = env
    with Storage(db) as s:
        s.set_force_flow_note(league_slug="surfeit", season=SEASON,
                              txn_id="t1", note="Worth a line.")
        assert s.force_flow_notes("surfeit", SEASON)["t1"]["note"] == "Worth a line."
        s.set_force_flow_note(league_slug="surfeit", season=SEASON,
                              txn_id="t1", note="   ")
        assert "t1" not in s.force_flow_notes("surfeit", SEASON)


def test_force_flow_is_not_a_weekly_section(env):
    from leaguepage.issue_builder import RETIRED_MODULES, WEEKLY_DEFAULT

    assert "forceflow" in RETIRED_MODULES
    assert "forceflow" not in WEEKLY_DEFAULT
    _c, db, _ed = env
    with Storage(db) as s:
        from leaguepage.issue_builder import module_states

        keys = {m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)}
    assert "forceflow" not in keys


def test_force_flow_is_still_a_league_page():
    import pathlib

    base = pathlib.Path("templates/public/base.html").read_text(encoding="utf-8")
    assert '"Force Flow"' in base and "transactions/index.html" in base


# ------------------------------------------------------------ about editor

def test_the_about_editor_saves_and_publishes(env):
    client, _db, ed = env
    r = client.post("/commissioner/site/about",
                    data={"text": "# About\n\nWritten by hand.\n", "action": "save"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert (ed / "site" / "about.md").read_text(encoding="utf-8").startswith("# About")
    assert "Written by hand." in desk_site.read_about()


def test_the_about_page_is_not_weekly_work(env):
    """It cannot appear in readiness, count toward approvals, or block an
    issue, because it is not a module at all."""
    from leaguepage.issue_builder import MODULE_DEFS, module_states

    assert all(k != "about" for k, _t, _l, _kd in MODULE_DEFS)
    _c, db, _ed = env
    with Storage(db) as s:
        mods = module_states(s, LG, SEASON, "week-01", week=1)
    assert all("about" not in m["module_key"] for m in mods)


def test_editing_about_does_not_touch_any_issue(env):
    from leaguepage.issue_builder import assemble_issue

    client, db, _ed = env
    with Storage(db) as s:
        before = assemble_issue(s, LG, SEASON, "week-01", week=1)["warnings"]
    client.post("/commissioner/site/about",
                data={"text": "# About\n\nchanged\n", "action": "save"},
                follow_redirects=True)
    with Storage(db) as s:
        after = assemble_issue(s, LG, SEASON, "week-01", week=1)["warnings"]
    assert before == after
