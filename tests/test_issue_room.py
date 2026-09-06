"""The Issue Room: a week's work in one place.

The acceptance test for the shell, expressed as the Commissioner's own
journey — open the issue, move between sections without a page load, see
what needs him, read the research, look at the real page, reach Publish.

The room shares the long-form editor's context, section card and endpoints
on purpose. These tests pin the sharing, because two surfaces that drift
into disagreeing about what a section is would be worse than the one long
column this replaces.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import REPO_ROOT, get_league
from leaguepage.desk import create_app
from leaguepage.desk_editor import _rail_state
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, save_section

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
ROOM = f"{BASE}/room"


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
    (idir / "lowdown").mkdir(parents=True)
    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nOriginal words.\n",
                                                 encoding="utf-8")
    (idir / "sections").mkdir()
    (idir / "proposals").mkdir()
    return TestClient(create_app(db_path=db)), db, idir


ATTR = r'data-{}="([a-z0-9:_-]+)"'          # the attribute, never a JS template literal


def _room(client):
    r = client.get(ROOM)
    assert r.status_code == 200, r.text[-800:]
    return r.text


def _rail(html):
    return html[html.index('aria-label="Sections in this issue"'):html.index("</nav>")]


def _make_ready(client, db):
    """One approved section and nothing else included: the smallest issue
    that passes every publication gate."""
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=0)
    r = client.post(f"{BASE}/edit/approve", json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text


# ------------------------------------------------- one issue, one page

def test_every_section_is_in_the_room_and_only_one_is_shown(env):
    """Moving between sections costs no request, so an unsaved box is
    never lost to navigation."""
    client, _db, _idir = env
    html = _room(client)
    panes = re.findall(ATTR.format("pane"), html)
    rails = re.findall(ATTR.format("rail"), html)
    assert set(panes) == set(rails) and len(panes) >= 8
    for key in ("lowdown", "ctp", "hardware"):
        assert key in panes, key
    # exactly one pane starts visible; the rest carry `hidden`
    opens = re.findall(r'<div class="pane" data-pane="[a-z0-9:_-]+"([^>]*)>', html)
    assert len(opens) == len(panes)
    assert sum(1 for o in opens if "hidden" not in o) == 1, opens
    # no anchor navigates to another editing page for a section
    assert f'href="{BASE}/edit#' not in html


def test_the_room_runs_the_paper_in_the_paper_order(env):
    client, _db, _idir = env
    html = _room(client)
    rail = _rail(html)
    weekly = rail[:rail.index("Administration")] if "Administration" in rail else rail
    order = re.findall(ATTR.format("rail"), weekly)
    assert order[0] == "lowdown", order
    assert order.index("ctp") < order.index("hardware")
    assert order[-1] == "hardware", "Weekly Hardware closes every issue"


def test_the_rail_says_what_needs_him_not_what_the_column_is_called(env):
    """Implementation words are not the visual language of the rail."""
    client, _db, idir = env
    html = _room(client)
    labels = set(re.findall(r'class="tok tok-\w+">([^<]+)<', _rail(html)))
    assert labels, "the rail carries state tokens"
    for jargon in ("commissioner-edited", "generated", "not_written", "prose_state"):
        assert jargon not in labels, jargon
    assert labels <= {"excluded", "automatic", "AI draft ready", "needs writing",
                      "needs review", "approved", "nothing this week"} | {
        l for l in labels if re.fullmatch(r"\d+/\d+ written", l)}, labels


@pytest.mark.parametrize("card,expected", [
    ({"included": False}, ("excluded", "off")),
    ({"included": True, "kind": "auto"}, ("automatic", "off")),
    ({"included": True, "proposal": "text"}, ("AI draft ready", "ai")),
    ({"included": True, "editable": True, "not_written": True}, ("needs writing", "work")),
    ({"included": True, "changed_since_approval": True}, ("needs review", "need")),
    ({"included": True, "empty": True}, ("nothing this week", "work")),
    ({"included": True, "approved": True}, ("approved", "ok")),
    ({"included": True, "children_total": 6, "children_written": 2}, ("2/6 written", "work")),
    ({"included": True, "children_total": 6}, ("0/6 written", "work")),
    ({"included": True, "children_total": 6, "children_written": 6, "approved": True},
     ("approved", "ok")),
])
def test_rail_state_precedence(card, expected):
    assert _rail_state(card) == expected


# ------------------------------------------------- shared implementation

def test_the_room_and_the_editor_are_one_implementation(env):
    """Same card partial, same script, same endpoints. A second copy of
    any of those is how the two surfaces start disagreeing."""
    room = pathlib.Path("templates/desk/issue_room.html").read_text(encoding="utf-8")
    editor = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    for tpl in (room, editor):
        assert '{% include "desk/_section_card.html" %}' in tpl
        assert '<script src="/static/desk-editor.js"></script>' in tpl
    # the behaviour lives in one file, not two inline blocks
    assert "async function saveOne" not in room and "async function saveOne" not in editor
    js = pathlib.Path("static/desk-editor.js").read_text(encoding="utf-8")
    for fn in ("saveAll", "approve", "proposal", "showRevisions", "replaceOrigin"):
        assert f"function {fn}(" in js, fn


def test_the_room_writes_through_the_same_endpoints(env):
    """Edit in the room, and the section's history, provenance and
    approval behave exactly as they do from the editor."""
    client, db, idir = env
    _room(client)
    r = save_section(client, f"{BASE}/edit", "lowdown", "Rewritten in the room.\n")
    assert r.status_code == 200, r.text
    assert (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8") == "Rewritten in the room.\n"
    revs = client.get(f"{BASE}/edit/revisions", params={"section": "lowdown"}).json()
    assert revs["revisions"], "history still records what was replaced"
    assert "Rewritten in the room." in _room(client)


def test_a_stale_box_in_a_second_tab_cannot_overwrite_newer_prose(env):
    """Two devices, one Commissioner. The room uses the same conflict
    check the editor does, so last-write-wins never happens silently."""
    client, _db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "First.\n")
    stale = client.post(f"{BASE}/edit/save",
                        json={"section": "lowdown", "text": "From the other tab.\n",
                              "expected_version": "fs1:0000000000000000"})
    assert stale.status_code == 409 and stale.json()["error"] == "conflict"


# ------------------------------------------------- context and publishing

def test_the_preview_pane_is_the_readers_renderer(env):
    client, _db, _idir = env
    html = _room(client)
    assert f'src="{BASE}/edit/full-preview"' in html
    assert "<iframe" in html
    # not production: production has no unpublished issue in it
    assert "vercel.app" not in html.split("<iframe")[1].split(">")[0]


def test_research_and_qa_ride_along_with_the_section(env):
    client, _db, _idir = env
    html = _room(client)
    assert set(re.findall(ATTR.format("research"), html)) == set(
        re.findall(ATTR.format("pane"), html))
    assert 'data-ctx="qa"' in html and "PUBLICATION CHECK" in html.upper()


def test_publish_is_reachable_without_leaving_the_room(env):
    client, db, _idir = env
    _make_ready(client, db)
    html = _room(client)
    assert "READY" in html
    assert 'id="pubdlg"' in html and "showModal()" in html
    assert f'action="{BASE}/edit/publish-start"' in html
    # every safeguard the full screen has
    assert 'name="confirm_deploy"' in html and "required" in html
    assert "confirm(" in html
    assert f'href="{BASE}/edit/publish"' in html, "the full screen is still one click away"


def test_the_room_and_the_publish_screen_read_one_publication_state(env):
    """Both call _add_publication_state; two readings that could disagree
    is the bug this project keeps having to fix."""
    src = pathlib.Path("leaguepage/desk_editor.py").read_text(encoding="utf-8")
    assert src.count("_add_publication_state(") == 3   # the def plus two callers
    client, db, _idir = env
    _make_ready(client, db)
    room = _room(client)
    screen = client.get(f"{BASE}/edit/publish").text
    assert "never published" in room and "never deployed" in screen


def test_blockers_refuse_publication_in_the_drawer_too(env):
    client, _db, _idir = env
    html = _room(client)          # nothing approved yet
    assert "BLOCKED" in html
    assert "Publishing is refused until the blockers clear." in html
    assert 'name="confirm_deploy"' not in html.split('id="pubdlg"')[1]


# ------------------------------------------------- safety and shape

def test_the_room_is_private_and_carries_no_new_public_surface():
    src = pathlib.Path("leaguepage/desk.py").read_text(encoding="utf-8")
    public = src[src.index("PUBLIC_PATHS = {"):]
    public = public[:public.index("}") + 1]
    assert "room" not in public and "desk-editor.js" not in public


def test_the_old_editor_still_works_during_migration(env):
    """Nothing is retired until the room has run a real week."""
    client, _db, _idir = env
    r = client.get(f"{BASE}/edit")
    assert r.status_code == 200 and "Original words." in r.text


def test_the_room_survives_an_issue_with_nothing_written_yet(env, tmp_path):
    client, _db, idir = env
    (idir / "lowdown" / "lowdown.md").unlink()
    html = _room(client)
    assert "needs writing" in html and "BLOCKED" in html


def test_the_publish_drawer_is_covered_by_the_central_csrf_wiring(env):
    """The drawer's form is an ordinary form in the document, so the
    document-level submit listener in desk.js attaches the token. A form
    that carried its own token, or lived outside the document, would be a
    second path to publishing."""
    client, db, _idir = env
    _make_ready(client, db)
    html = _room(client)
    assert '<meta name="csrf-token"' in html
    assert '/static/desk.js' in html
    drawer = html[html.index('id="pubdlg"'):html.index("</dialog>")]
    assert 'action="' in drawer and "csrf_token" not in drawer
    js = pathlib.Path("static/desk.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("submit"' in js


# ------------------------------------------------- two devices, one issue

def test_a_conflict_carries_both_sides_so_neither_is_lost(env):
    """"Reload the page" used to be the whole answer, and it threw away
    whatever he had just typed. The refusal now says what he was editing,
    what is stored, and what the stored text actually is."""
    client, _db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "First from the laptop.\n")
    stale = client.post(f"{BASE}/edit/save",
                        json={"section": "lowdown", "text": "From the phone.\n",
                              "expected_version": "fs1:" + "0" * 16})
    assert stale.status_code == 409
    body = stale.json()
    assert body["expected_version"] == "fs1:" + "0" * 16
    assert body["current_version"] and body["current_version"] != body["expected_version"]
    assert body["current_text"] == "First from the laptop.\n"
    assert "changed elsewhere" in body["message"]


def test_a_refused_save_leaves_the_approval_describing_the_stored_text(env):
    """An approval must describe the stored text. A conflict is not an
    edit, so it must not retire a sign-off that still describes what is
    actually stored. (What binds it is the save route, not a signature --
    see `test_ordinary_approval_is_a_flag_and_not_a_signature`.)"""
    client, db, _idir = env
    state = client.get(f"{BASE}/edit/section-state",
                       params={"section": "lowdown"}).json()
    r = client.post(f"{BASE}/edit/approve",
                    json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text

    def approved():
        with Storage(db) as s:
            row = s.get_issue_modules("surfeit", SEASON, "week-01").get("lowdown") or {}
        return bool(row.get("approved"))

    assert approved()
    stale = client.post(f"{BASE}/edit/save",
                        json={"section": "lowdown", "text": "Rewritten elsewhere.\n",
                              "expected_version": "fs1:" + "0" * 16})
    assert stale.status_code == 409
    assert approved(), "a save that never happened cannot retire an approval"
    # and the real edit still does
    save_section(client, f"{BASE}/edit", "lowdown", "Rewritten properly.\n")
    assert not approved()
    assert state["version"]


def test_a_refused_save_writes_no_history_and_no_provenance(env):
    """Nothing downstream of the write runs when the write is refused."""
    client, db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "One.\n")
    before = client.get(f"{BASE}/edit/revisions", params={"section": "lowdown"}).json()
    client.post(f"{BASE}/edit/save",
                json={"section": "lowdown", "text": "Two.\n",
                      "expected_version": "fs1:" + "0" * 16})
    after = client.get(f"{BASE}/edit/revisions", params={"section": "lowdown"}).json()
    assert len(after["revisions"]) == len(before["revisions"])


def test_the_editor_carries_a_version_into_every_box_and_back(env):
    """The browser cannot base a save on a version the page never gave it."""
    client, _db, _idir = env
    html = client.get(f"{BASE}/room").text
    assert 'data-version="fs1:' in html
    js = (REPO_ROOT / "static" / "desk-editor.js").read_text(encoding="utf-8")
    assert "expected_version: ta.dataset.version" in js
    # A save that succeeds hands back the next version, or the autosave a
    # second later would be refused by the store it had just written to.
    assert "if (data.version) ta.dataset.version = data.version;" in js
    # And a refused one stops the timer rather than retrying forever.
    assert "if (ta.dataset.conflict) return false;" in js
    assert "filter((ta) => !ta.dataset.conflict)" in js
    assert "showConflict" in js


def test_accepting_a_proposal_that_changed_under_review_is_refused(env):
    """A second Claude run can rewrite the proposal while the review page
    is open. Accepting must publish what he read, or refuse."""
    client, _db, idir = env
    (idir / "proposals" / "lowdown.md").write_text("The draft he read.\n",
                                                   encoding="utf-8")
    seen = client.get(f"{BASE}/edit/section-state", params={"section": "lowdown"})
    assert seen.status_code == 200
    (idir / "proposals" / "lowdown.md").write_text("A different draft entirely.\n",
                                                   encoding="utf-8")
    r = client.post(f"{BASE}/edit/proposal",
                    json={"section": "lowdown", "action": "accept",
                          "proposal_version": "fs1:" + "0" * 16})
    assert r.status_code == 409
    assert client.get(f"{BASE}/edit/section-state",
                      params={"section": "lowdown"}).json()["text"] \
        == "# The Lowdown\n\nOriginal words.\n"


def test_accepting_a_proposal_retires_another_tabs_version(env):
    """Accepting replaces the section outright. A tab that was editing the
    old text must be refused rather than quietly undoing the acceptance."""
    client, _db, idir = env
    edit = f"{BASE}/edit"
    open_tab = client.get(f"{edit}/section-state",
                          params={"section": "lowdown"}).json()["version"]
    (idir / "proposals" / "lowdown.md").write_text("Claude's draft.\n",
                                                   encoding="utf-8")
    r = client.post(f"{edit}/proposal",
                    json={"section": "lowdown", "action": "accept"})
    assert r.status_code == 200, r.text

    def stored():
        return client.get(f"{edit}/section-state",
                          params={"section": "lowdown"}).json()["text"].strip()

    # Accepting strips the draft scaffolding, trailing newline included.
    assert stored() == "Claude's draft."
    stale = client.post(f"{edit}/save",
                        json={"section": "lowdown", "text": "From the old tab.\n",
                              "expected_version": open_tab})
    assert stale.status_code == 409
    assert stored() == "Claude's draft."


def test_clearing_a_section_still_moves_its_version(env):
    """Emptying the box is a change like any other. Another tab holding
    the old version must not be able to put the old text back."""
    client, _db, _idir = env
    edit = f"{BASE}/edit"
    before = client.get(f"{edit}/section-state",
                        params={"section": "lowdown"}).json()["version"]
    save_section(client, edit, "lowdown", "")
    after = client.get(f"{edit}/section-state",
                       params={"section": "lowdown"}).json()
    assert after["exists"] and after["text"] == "" and after["version"] != before
    stale = client.post(f"{edit}/save",
                        json={"section": "lowdown", "text": "Old words return.\n",
                              "expected_version": before})
    assert stale.status_code == 409


def test_the_history_panel_asks_the_repository_and_not_the_database(env,
                                                                    monkeypatch):
    """Whichever store holds the prose holds its undo history.

    Both of these routes used to read `prose_revisions` out of SQLite
    directly. That was indistinguishable from correct while the
    filesystem backend was authoritative, because the filesystem backend
    keeps its revisions in SQLite -- and it would have emptied the History
    panel the day a Postgres cutover happened.
    """
    from leaguepage import prose_store

    client, _db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "One.\n")
    save_section(client, f"{BASE}/edit", "lowdown", "Two.\n")

    seen = {}

    def fake_history(self, key, limit=10):
        seen["key"] = str(key)
        return [{"id": 4242, "source": "commissioner-save",
                 "prior_text": "from the repository", "created_at": "2026-01-01"}]

    monkeypatch.setattr(prose_store.FilesystemProseRepository, "history",
                        fake_history)
    body = client.get(f"{BASE}/edit/revisions",
                      params={"section": "lowdown"}).json()
    assert [r["id"] for r in body["revisions"]] == [4242]
    assert body["revisions"][0]["preview"] == "from the repository"
    assert "lowdown" in seen["key"]


def test_restore_reads_the_revision_through_the_repository(env, monkeypatch):
    """Same coupling, other half: Restore fetched the row from SQLite."""
    from leaguepage import prose_store

    client, _db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "One.\n")
    save_section(client, f"{BASE}/edit", "lowdown", "Two.\n")

    asked = []
    real = prose_store.FilesystemProseRepository.revision

    def watched(self, revision_id):
        asked.append(revision_id)
        return real(self, revision_id)

    monkeypatch.setattr(prose_store.FilesystemProseRepository, "revision",
                        watched)
    rows = client.get(f"{BASE}/edit/revisions",
                      params={"section": "lowdown"}).json()["revisions"]
    state = client.get(f"{BASE}/edit/section-state",
                       params={"section": "lowdown"}).json()
    # newest first, so rows[0] is the text the second save replaced
    r = client.post(f"{BASE}/edit/restore",
                    json={"section": "lowdown", "revision_id": rows[0]["id"],
                          "expected_version": state["version"]})
    assert r.status_code == 200, r.text
    assert asked == [rows[0]["id"]]
    assert client.get(f"{BASE}/edit/section-state",
                      params={"section": "lowdown"}).json()["text"] == "One.\n"


def test_ordinary_approval_is_a_flag_and_not_a_signature(env):
    """A known gap, pinned here so it cannot be quietly lost.

    Common Tactical Picture signs its approval over the exact text it
    covers, so editing any preview retires that sign-off with nothing
    having to notice. An ordinary section's approval is a boolean in
    `issue_modules`, and the save route clears it AFTER the prose write.

    Every path through the Desk clears it, so the Desk is right today.
    What is not proved is the storage layer: prose and approval live in
    two stores with no shared transaction, so a prose write that lands
    beside a metadata write that does not leaves `approved = 1` standing
    over text nobody approved. This is one of the two reasons the Postgres
    prose backend is not authoritative.

    When approval becomes content-bound, this test should fail, and
    `docs/COMMISSIONER_PORTAL_ARCHITECTURE.md` should change with it.
    """
    client, db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "Approved words.\n")
    assert client.post(f"{BASE}/edit/approve",
                       json={"section": "lowdown",
                             "action": "approve"}).status_code == 200

    def module_row():
        with Storage(db) as s:
            return s.get_issue_modules("surfeit", SEASON, "week-01").get("lowdown") or {}

    assert module_row().get("approved")
    # Exactly the partial write the two stores allow: the prose moves and
    # the metadata write that follows it never runs.
    from leaguepage import prose_store

    repo = prose_store.repository(db)
    key = prose_store.ProseKey.section("surfeit", SEASON, "week-01", "lowdown")
    repo.put(key, "Different words nobody signed off.\n",
             expected_version=repo.get(key).version)

    row = module_row()
    assert row.get("approved"), "pinning the gap, not endorsing it"
    assert row.get("approved_sha") in (None, ""), \
        "an ordinary approval carries no signature over its text"
