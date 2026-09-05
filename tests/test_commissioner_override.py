"""Automation supplies the default. It never takes the pen away.

The Desk had drifted into presenting assembled sections as finished
business. A card reading `Rendered automatically; nothing to write`, a
Lowdown whose summary said only `commissioner draft present`, and a Common
Tactical Picture showing `6 / 6 approved` all looked like results rather
than drafts, and the only way to find out otherwise was to expand one and
hope. Worse, editing an approved section left the approval standing, so a
green chip could sit over text nobody had signed off.

The rule these tests hold in place:

    every piece of prose the newsletter publishes can be changed from the
    screen it appears on; computed results stay computed; and an edit after
    approval takes the approval back.

They drive the rendered Desk rather than the helpers underneath it. "The
function exists" was never the half that was failing.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import provenance, section_defaults
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import assemble_issue, module_states
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
EDIT = f"{BASE}/edit"

# Two awards the synthetic week actually produces nominees for. Decided
# awards are what Weekly Hardware is composed from, so the fixture has to
# decide some for there to be a generated version at all.
DECIDED = (("hard-luck-bastard", "team-9"), ("escape-artist", "team-2"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The state in his screenshot: a Lowdown he has drafted, a Hardware
    section standing on generated copy, and every matchup approved."""
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid * 3 for rid in range(1, 11)})
        s.set_meta("current_week", "1")
        for key, winner in DECIDED:
            s.set_award_decision(league_slug="surfeit", season=SEASON,
                                 workflow="week-01", award_key=key,
                                 decision="awarded", winner=winner,
                                 note="private steering, never publishes")
    client = TestClient(create_app(db_path=db))

    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "lowdown").mkdir(parents=True, exist_ok=True)
    (idir / "lowdown" / "lowdown.md").write_text(
        "An opening the Commissioner wrote himself.\n", encoding="utf-8")
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        "A Claude rough draft of the same week.\n", encoding="utf-8")

    with Storage(db) as s:
        kids = ib.matchup_children(s, LG, SEASON, "week-01", week=1)
    for c in kids:
        d = idir / "matchups" / c["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.md").write_text(f"Preview of {c['slug']}.\n", encoding="utf-8")
        r = client.post(f"{EDIT}/approve",
                        json={"section": c["section"], "action": "approve"})
        assert r.status_code == 200, r.text
    assert client.post(f"{EDIT}/approve",
                       json={"section": "ctp", "action": "approve"}).status_code == 200
    # Hardware standing on the generated version, installed the way the
    # Desk installs it.
    assert client.post(f"{EDIT}/reset-generated",
                       json={"section": "hardware", "confirm": "yes"}).status_code == 200
    assert client.post(f"{EDIT}/approve",
                       json={"section": "hardware", "action": "approve"}).status_code == 200
    assert client.post(f"{EDIT}/approve",
                       json={"section": "lowdown", "action": "approve"}).status_code == 200
    return client, db, ed


def _page(client):
    r = client.get(EDIT)
    assert r.status_code == 200, r.text[:400]
    return r.text


# Top-level cards only: a matchup preview is `sec card child`, and it lives
# INSIDE its parent, so slicing at the next child would cut CTP in half.
_NEXT_CARD = re.compile(r'<details class="sec card(?: admin)?" id="sec-')


def _card(html, key):
    """The markup of one card, from its <details> to the next top-level one."""
    start = html.index(f'id="sec-{key}"')
    rest = html[start:]
    end = _NEXT_CARD.search(rest)
    return rest[:end.start()] if end else rest


def _editors(html):
    return set(re.findall(
        r'<textarea class="prose autosave" data-section="([a-z0-9:.-]+)"', html))


def _approved(db, key):
    with Storage(db) as s:
        return bool((s.get_issue_modules("surfeit", SEASON, "week-01")
                     .get(key) or {}).get("approved"))


def _matchup_status(db, slug):
    with Storage(db) as s:
        return (s.get_matchup_state(league_slug="surfeit", season=SEASON,
                                    week=1, matchup_slug=slug) or {})["status"]


def _first_child(db):
    with Storage(db) as s:
        return ib.matchup_children(s, LG, SEASON, "week-01", week=1)[0]


def _save(client, section, text, sha=""):
    return client.post(f"{EDIT}/save",
                       json={"section": section, "text": text, "base_sha": sha})


# =================================================== the screenshot, expanded

def test_the_lowdown_card_contains_the_actual_editor(env):
    """`commissioner draft present` is a status line, not a locked door."""
    client, _db, _ed = env
    card = _card(_page(client), "lowdown")
    assert "lowdown" in _editors(card)
    assert "An opening the Commissioner wrote himself." in card
    for control in ("previewSection('lowdown')", "showRevisions('lowdown')",
                    "approve('lowdown', true)", "approve('lowdown', false)",
                    "askRewrite('lowdown')", "resetGenerated('lowdown')"):
        assert control in card, control


def test_the_lowdown_card_says_who_wrote_what_is_in_it(env):
    client, _db, _ed = env
    card = _card(_page(client), "lowdown")
    assert 'class="authority"' in card
    assert "Edit it in the box below." in card


def test_weekly_hardware_shows_its_generated_copy_and_how_to_change_it(env):
    """It was the card that read `Rendered automatically; nothing to write`.
    Expanded, it now shows the composed copy in an editor, the results it
    was composed from, and a way back to them."""
    client, _db, _ed = env
    card = _card(_page(client), "hardware")
    assert "hardware" in _editors(card)
    assert "Hard-Luck Bastard" in card, "the generated copy is in the editor"
    assert "Computed evidence" in card
    assert "resetGenerated('hardware')" in card
    assert "nothing to write" not in card


def test_the_hardware_evidence_is_shown_but_not_typed_into(env):
    """Results are shown; readings are edited. The evidence panel must not
    turn a computed award into an editable field."""
    client, _db, _ed = env
    card = _card(_page(client), "hardware")
    evidence = card[card.index("Computed evidence"):]
    evidence = evidence[:evidence.index("</details>")]
    assert "<input" not in evidence and "<textarea" not in evidence


def test_ctp_children_stay_editable_at_five_of_five_approved(env):
    """`5 / 5 approved` describes the previews. It does not close them."""
    client, db, _ed = env
    card = _card(_page(client), "ctp")
    assert "5 / 5 approved" in card
    editors = _editors(card)
    with Storage(db) as s:
        kids = ib.matchup_children(s, LG, SEASON, "week-01", week=1)
    assert len(kids) == 5
    for c in kids:
        assert c["approved"], "fixture: every matchup approved"
        assert c["section"] in editors, c["section"]


def test_every_weekly_card_with_public_prose_offers_an_editor(env):
    """The audit, as a test. A module that publishes prose and gives him no
    way to change it is the defect; the exceptions are the modules that
    publish no prose at all."""
    client, db, _ed = env
    html = _page(client)
    editors = _editors(html)
    with Storage(db) as s:
        mods = module_states(s, LG, SEASON, "week-01", week=1)
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    publishes = {x["module_key"] for x in assembled["sections"]
                 if x["kind"] != "auto" and x.get("content_md")}
    for m in mods:
        if m["module_key"] not in publishes:
            continue
        assert m["module_key"] in editors, (
            f"{m['module_key']} publishes prose with no editor on the Desk")


# ============================================== approval follows the content

def test_editing_an_approved_matchup_unapproves_it_and_ctp(env):
    client, db, _ed = env
    child = _first_child(db)
    assert child["approved"] and _approved(db, "ctp")
    r = _save(client, child["section"], "He rewrote this one.\n")
    assert r.status_code == 200, r.text
    assert _matchup_status(db, child["slug"]) == "edited"
    assert not _approved(db, "ctp"), "CTP publishes the previews; its sign-off went too"


def test_an_edited_matchup_is_marked_as_changed_on_the_page(env):
    client, db, _ed = env
    child = _first_child(db)
    _save(client, child["section"], "He rewrote this one.\n")
    html = _page(client)
    assert "changed since approval" in html
    assert "changed since approval" in _card(html, "ctp")


def test_editing_approved_hardware_unapproves_it(env):
    client, db, _ed = env
    assert _approved(db, "hardware")
    _save(client, "hardware", "### Hardware, in my own words\n\nMine.\n")
    assert not _approved(db, "hardware")


def test_editing_the_approved_lowdown_unapproves_it(env):
    client, db, _ed = env
    assert _approved(db, "lowdown")
    _save(client, "lowdown", "A different opening entirely.\n")
    assert not _approved(db, "lowdown")


def test_re_approving_clears_the_changed_mark(env):
    client, db, _ed = env
    _save(client, "hardware", "### Mine\n\nMine.\n")
    assert "changed since approval" in _card(_page(client), "hardware")
    assert client.post(f"{EDIT}/approve",
                       json={"section": "hardware", "action": "approve"}).status_code == 200
    assert "changed since approval" not in _card(_page(client), "hardware")


def test_restoring_an_old_revision_also_retires_the_approval(env):
    """Restoring is an edit. It replaces the text that was approved."""
    client, db, _ed = env
    _save(client, "hardware", "### Mine\n\nMine.\n")
    client.post(f"{EDIT}/approve", json={"section": "hardware", "action": "approve"})
    revs = client.get(f"{EDIT}/revisions", params={"section": "hardware"}).json()
    assert revs["revisions"], "the override snapshotted the generated copy"
    r = client.post(f"{EDIT}/restore",
                    json={"section": "hardware",
                          "revision_id": revs["revisions"][0]["id"]})
    assert r.status_code == 200, r.text
    assert not _approved(db, "hardware")


def test_an_unapproved_section_edit_changes_no_approval(env):
    """The rule is "retire the sign-off that covered this", not "clear
    everything on every keystroke"."""
    client, db, _ed = env
    assert _approved(db, "lowdown")
    _save(client, "tracks", "Tracks copy.\n")
    assert _approved(db, "lowdown"), "an edit here says nothing about that"


# ======================================================= override and restore

def test_the_generated_copy_is_composed_only_from_decided_awards(env):
    client, db, _ed = env
    with Storage(db) as s:
        rows = section_defaults.hardware_evidence(s, LG, SEASON, "week-01")
        md = section_defaults.compose_hardware(rows)
    assert {r["award_key"] for r in rows} == {k for k, _w in DECIDED}
    assert "Hard-Luck Bastard" in md and "Escape Artist" in md


def test_a_private_award_note_never_reaches_the_composed_copy(env):
    """The note is his steering for a writing brief. It was never for a
    reader, and composed copy publishes."""
    client, db, _ed = env
    with Storage(db) as s:
        md = section_defaults.generated_md(s, LG, SEASON, "week-01", "hardware")
    assert "private steering" not in md


def test_an_override_is_never_destroyed_by_resetting(env):
    client, db, _ed = env
    _save(client, "hardware", "### My own Hardware\n\nWords I chose.\n")
    r = client.post(f"{EDIT}/reset-generated",
                    json={"section": "hardware", "confirm": "yes"})
    assert r.status_code == 200, r.text
    revs = client.get(f"{EDIT}/revisions", params={"section": "hardware"}).json()
    assert any("My own Hardware" in x["preview"] for x in revs["revisions"]), \
        "his words must still be restorable from History"


def test_reset_needs_confirmation(env):
    client, _db, _ed = env
    r = client.post(f"{EDIT}/reset-generated", json={"section": "hardware"})
    assert r.status_code == 400


def test_a_section_with_no_generated_version_says_so(env):
    client, _db, _ed = env
    r = client.post(f"{EDIT}/reset-generated",
                    json={"section": "tracks", "confirm": "yes"})
    assert r.status_code == 400
    assert "no generated version" in r.json()["error"]


# =========================================================== provenance truth

def test_generated_hardware_is_labelled_as_machine_written_not_as_ai(env):
    """Our own code composed it from results. Badging it "by Claude Code"
    would name a writer that was not involved."""
    client, db, _ed = env
    with Storage(db) as s:
        text = section_defaults.generated_md(s, LG, SEASON, "week-01", "hardware")
        state = provenance.state_for(s, league_slug="surfeit", season=SEASON,
                                     issue_key="week-01", section="hardware",
                                     text=text)
    assert state is not None
    assert state["badge_text"] == "AUTO"
    assert state["generator"] is None
    assert "AI-generated" not in state["caption"]


def test_editing_the_generated_hardware_makes_it_generated_then_edited(env):
    """Origin stays deterministic however much he rewrites; the label says
    he edited it and the Desk shows roughly how much."""
    client, db, _ed = env
    new = "### Weekly Hardware\n\nI rewrote every word of this.\n"
    _save(client, "hardware", new)
    with Storage(db) as s:
        st = provenance.state_for(s, league_slug="surfeit", season=SEASON,
                                  issue_key="week-01", section="hardware", text=new)
    assert st["label"] == "Automatically generated · Commish edited"
    assert st["badge_text"] == "AUTO" and st["generator"] is None


def test_resetting_to_generated_makes_the_claim_true_again(env):
    """Exact equality by hash, not a resemblance. The claim comes back
    because the text is byte-for-byte what was recorded."""
    client, db, _ed = env
    _save(client, "hardware", "### Mine\n\nMine.\n")
    client.post(f"{EDIT}/reset-generated",
                json={"section": "hardware", "confirm": "yes"})
    with Storage(db) as s:
        text = section_defaults.generated_md(s, LG, SEASON, "week-01", "hardware")
        assert provenance.state_for(s, league_slug="surfeit", season=SEASON,
                                    issue_key="week-01", section="hardware",
                                    text=text) is not None


def test_restoring_a_claude_draft_invents_no_claim(env):
    """Nothing recorded that rough-lowdown.md was Claude's, so resetting to
    it must not start saying so."""
    client, db, _ed = env
    r = client.post(f"{EDIT}/reset-generated",
                    json={"section": "lowdown", "confirm": "yes"})
    assert r.status_code == 200, r.text
    with Storage(db) as s:
        assert provenance.state_for(
            s, league_slug="surfeit", season=SEASON, issue_key="week-01",
            section="lowdown",
            text="A Claude rough draft of the same week.\n") is None


def test_the_card_reports_generated_then_edited(env):
    client, _db, _ed = env
    assert "exact generated baseline" in _card(_page(client), "hardware")
    _save(client, "hardware", "### Mine\n\nMine now.\n")
    assert "changed from generated baseline" in _card(_page(client), "hardware")


# =============================================== optional blurbs block nothing

def test_ctp_offers_an_optional_intro(env):
    client, _db, _ed = env
    card = _card(_page(client), "ctp")
    assert "ctp" in _editors(card)
    assert "Opening remarks" in card


def test_an_absent_ctp_intro_blocks_neither_approval_nor_publication(env):
    client, db, _ed = env
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    kinds = {r["kind"] for r in assembled["warning_rows"] if r["module_key"] == "ctp"}
    assert not kinds, kinds
    assert _approved(db, "ctp")


def test_a_ctp_intro_publishes_above_the_previews(env):
    client, db, _ed = env
    _save(client, "ctp", "Five games, one of them worth watching.\n")
    client.post(f"{EDIT}/approve", json={"section": "ctp", "action": "approve"})
    with Storage(db) as s:
        body = next(x["content_md"] for x in
                    assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                    if x["module_key"] == "ctp")
    assert "Five games, one of them worth watching." in body
    assert body.index("Five games") < body.index("###"), "the previews still follow it"


def test_the_power_blurb_is_editable_from_the_card(env):
    """It was already read into the published section and had no editor at
    all: the only way to change it was to open the file."""
    client, db, _ed = env
    with Storage(db) as s:
        s.save_power_rankings("surfeit", SEASON, "week-01",
                              [{"roster_id": i, "rank": i, "tier": 1, "note": "x"}
                               for i in range(1, 11)])
    card = _card(_page(client), "power")
    assert "power" in _editors(card)
    _save(client, "power", "The order changed, and here is why.\n")
    with Storage(db) as s:
        body = next(x["content_md"] for x in
                    assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                    if x["module_key"] == "power")
    assert body.startswith("The order changed, and here is why.")


def test_a_blurb_edit_retires_the_sections_approval(env):
    client, db, _ed = env
    _save(client, "ctp", "An opening line.\n")
    assert not _approved(db, "ctp")


# ============================================ retired sections still publish

def test_a_retired_section_this_issue_carries_is_still_editable(env):
    """Force Flow stopped being a weekly section. An issue that already has
    one still publishes its prose, which makes that prose his."""
    client, db, ed = env
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "sections").mkdir(parents=True, exist_ok=True)
    (idir / "sections" / "forceflow.md").write_text("Old copy.\n", encoding="utf-8")
    with Storage(db) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON,
                           issue_key="week-01", module_key="forceflow", included=1)
    card = _card(_page(client), "forceflow")
    assert "forceflow" in _editors(card)
    assert "Old copy." in card
    _save(client, "forceflow", "Rewritten by hand.\n")
    assert (idir / "sections" / "forceflow.md").read_text(
        encoding="utf-8") == "Rewritten by hand.\n"


# ================================================================ regressions

def test_the_masthead_still_claims_nothing_to_edit(env):
    """The one true exception: it prints league theme and issue metadata,
    and there is no prose in it."""
    client, _db, _ed = env
    card = _card(_page(client), "masthead")
    assert "masthead" not in _editors(card)
    assert "nothing here to edit or override" in card


def test_every_card_is_still_closed_on_load(env):
    """Discoverability was not licence to reopen eleven cards."""
    client, _db, _ed = env
    html = _page(client)
    body = html[html.index("</style>"):]
    for m in re.finditer(r"<details[^>]*>", body):
        assert " open" not in m.group(0), m.group(0)[:90]



def test_ctp_wears_no_parent_badge_and_each_preview_carries_its_own(env):
    """One badge over six pieces of writing describes none of them. Each
    preview's line sits under its own heading inside the section, his
    opening carries its own, and the section heading stays silent."""
    client, db, _ed = env
    with Storage(db) as s:
        for c in ib.matchup_children(s, LG, SEASON, "week-01", week=1):
            provenance.record(s, league_slug="surfeit", season=SEASON,
                              issue_key="week-01", section=c["section"],
                              generator="claude-code", method="matchup-brief",
                              text=f"Preview of {c['slug']}.\n")
        n = len(ib.matchup_children(s, LG, SEASON, "week-01", week=1))
        body = next(x["content_md"] for x in
                    assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                    if x["module_key"] == "ctp")
        assert body.count('class="prov"') == n
        assert body.count("AI-generated") == n
        assert provenance.section_state(s, league_slug="surfeit", season=SEASON,
                                        issue_key="week-01", section="ctp",
                                        text=body) is None
    _save(client, "ctp", "My own way in.\n")
    with Storage(db) as s:
        body = next(x["content_md"] for x in
                    assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                    if x["module_key"] == "ctp")
        assert body.count('class="prov"') == n + 1
        assert body.index("Commish-written") < body.index("My own way in.")
        assert provenance.section_state(s, league_slug="surfeit", season=SEASON,
                                        issue_key="week-01", section="ctp",
                                        text=body) is None


def test_the_badge_survives_the_trip_through_assembly(env):
    """The claim is checked against the assembled section, not the file, so
    a stray strip or a trailing newline anywhere in between would silently
    drop the label. Assemble it the way publication does and look."""
    client, db, _ed = env
    with Storage(db) as s:
        sections = assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
        found = provenance.for_sections(s, "surfeit", SEASON, "week-01", sections)
    assert found.get("hardware"), "generated Hardware lost its label in assembly"
    assert found["hardware"]["badge_text"] == "AUTO"


def test_publication_is_refused_until_the_edit_is_re_approved(env):
    """The chip is a label; this is the gate. Editing an approved preview
    must actually stop the issue going out until he looks again."""
    client, db, _ed = env
    child = _first_child(db)
    _save(client, child["section"], "Rewritten, unread by anyone.\n")
    with Storage(db) as s:
        with pytest.raises(ValueError, match="not approved"):
            assemble_issue(s, LG, SEASON, "week-01", week=1, enforce=True)
    client.post(f"{EDIT}/approve",
                json={"section": child["section"], "action": "approve"})
    client.post(f"{EDIT}/approve", json={"section": "ctp", "action": "approve"})
    with Storage(db) as s:
        warnings = assemble_issue(s, LG, SEASON, "week-01", week=1)["warnings"]
    assert not [w for w in warnings if "Common Tactical Picture" in w]


def test_the_composed_copy_passes_publication_qa(env):
    """Composed prose publishes, so it goes through the same privacy and
    accuracy gate as anything he writes."""
    from leaguepage import pubqa

    client, db, _ed = env
    with Storage(db) as s:
        report = pubqa.check_issue(s, LG, SEASON, "week-01", week=1)
        hardware = next(x for x in
                        assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                        if x["module_key"] == "hardware")
    assert hardware["content_md"], "the fixture is standing on generated copy"
    assert not [b for b in report["blockers"]
                if b.get("module_key") == "hardware"], report["blockers"]
