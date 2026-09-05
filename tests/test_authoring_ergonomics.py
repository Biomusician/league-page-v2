"""Writing tools on the Desk: formatting, and handing work to Claude Code.

Two things the Commissioner asked for, and one thing neither of them is
allowed to become.

Bold and italic are buttons and keystrokes, not a rich-text editor. The
source stays Markdown, because that is what publishes, what diffs, and what
he can still fix by hand at 11pm. The buttons only type the asterisks.

Drafting is a prompt he copies, not an API this product calls. There is no
model key anywhere in League Page and there is not going to be one: Claude
Code is the editorial AI, he starts the session himself, and the Desk's job
ends at handing him the right prompt.

The prompt names files instead of pasting them. That is what keeps the
research private: possible moves, roast ammo, ghost briefs, private notes
and internal evidence all live in files on his machine, and nothing but
paths and instructions ever travels in the clipboard.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import matchup_children
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"

# The editing behaviour and the section markup are shared files now: the
# Issue Room and the long-form editor include the same ones, so these
# ergonomics hold on both surfaces rather than on whichever page they were
# written into.
EDITOR = pathlib.Path("static/desk-editor.js")
SECTION = pathlib.Path("templates/desk/_section_card.html")
CARD = pathlib.Path("templates/desk/_matchup_card.html")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    return TestClient(create_app(db_path=db)), db


def _js() -> str:
    return EDITOR.read_text(encoding="utf-8")


# ------------------------------------------------------------ bold / italic

def test_every_prose_box_gets_a_format_bar(env):
    js = _js()
    assert "function addFormatBar" in js
    assert 'document.querySelectorAll("textarea.autosave").forEach' in js
    assert "addFormatBar(ta)" in js


def test_bold_and_italic_are_the_two_buttons(env):
    js = _js()
    bar = js[js.index("function addFormatBar"):js.index("document.querySelectorAll(\"textarea.autosave\")")]
    assert '["B", "**", "Bold (Ctrl+B)"]' in bar
    assert '["I", "*", "Italic (Ctrl+I)"]' in bar


def test_the_keyboard_shortcuts_are_wired(env):
    js = _js()
    assert "e.ctrlKey || e.metaKey" in js
    assert 'wrapSelection(ta, k === "b" ? "**" : "*")' in js
    assert "e.preventDefault()" in js


def test_formatting_marks_the_section_dirty_so_it_autosaves(env):
    """Typing the asterisks by hand fires `input`; a button that skipped it
    would leave the change unsaved and look like it had not happened."""
    js = _js()
    body = js[js.index("function wrapSelection"):js.index("function addFormatBar")]
    assert 'new Event("input", {bubbles: true})' in body


def test_no_rich_text_editor_was_added(env):
    """The requirement was formatting help, not a new document model. A
    WYSIWYG would own the content and Markdown would stop being the source."""
    js = _js()
    for banned in ("contenteditable", "execCommand('bold')", "quill", "Quill",
                   "tinymce", "TinyMCE", "ProseMirror", "CKEditor", "trix"):
        assert banned not in js, banned
    # the editing surface is still a plain textarea
    assert "textarea.autosave" in js


def test_the_page_pulls_in_no_editor_library(env):
    """Its own two local scripts, and nothing off this machine."""
    client, _db = env
    html = client.get(EDIT).text
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert all(s.startswith("/static/") for s in srcs), srcs


# ------------------------------------------------------------ copy a prompt

def test_the_draft_button_copies_a_prompt(env):
    for tpl in (SECTION, CARD):
        text = tpl.read_text(encoding="utf-8")
        assert "Copy prompt for Claude" in text, tpl.name
        assert "Request Claude draft" not in text, tpl.name
        assert "askDraft" not in text, tpl.name


def test_it_reaches_no_model_api(env):
    js = _js()
    for banned in ("api.anthropic.com", "openai", "OPENAI", "ANTHROPIC_API_KEY",
                   "Bearer ", "x-api-key"):
        assert banned not in js, banned
    body = js[js.index("async function copyPrompt"):js.index("function showPromptToCopy")]
    assert body.count("fetch(") == 1
    assert 'EDIT + "/claude-prompt' in body


def test_a_refused_clipboard_still_shows_the_text(env):
    """A button that silently does nothing is the exact failure this Desk
    has already shipped once."""
    js = _js()
    assert "showPromptToCopy" in js
    body = js[js.index("function showPromptToCopy"):]
    assert "ta.select()" in body


def test_the_prompt_names_the_section_and_where_to_write_it(env):
    client, _db = env
    r = client.get(f"{EDIT}/claude-prompt", params={"section": "tracks"})
    assert r.status_code == 200, r.text
    p = r.json()["prompt"]
    assert "Tracks of Interest" in p
    assert ".claude/skills/my-writing-style/SKILL.md" in p
    assert "proposals/tracks.md" in p
    assert "AUTHORING-tracks.md" in p


def test_the_prompt_sends_claude_to_proposals_not_to_his_text(env):
    """His text is authoritative until he accepts a proposal on the Desk."""
    client, _db = env
    p = client.get(f"{EDIT}/claude-prompt", params={"section": "tracks"}).json()["prompt"]
    assert "Write the full section to `" in p and "proposals/tracks.md`" in p
    assert "Do not touch `" in p and "sections/tracks.md`" in p
    # and it points at the proposal before it says not to touch his file
    assert p.index("proposals/tracks.md") < p.index("Do not touch")


def test_the_prompt_requires_the_rough_draft_marker(env):
    """Nothing Claude writes may publish without passing through him."""
    client, _db = env
    p = client.get(f"{EDIT}/claude-prompt", params={"section": "tracks"}).json()["prompt"]
    assert "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED" in p


def test_a_matchup_child_gets_its_own_prompt(env):
    client, db = env
    with Storage(db) as s:
        kid = matchup_children(s, LG, SEASON, "week-01", 1)[0]
    r = client.get(f"{EDIT}/claude-prompt", params={"section": kid["section"]})
    assert r.status_code == 200, r.text
    p = r.json()["prompt"]
    assert kid["title"] in p
    assert f"matchups/{kid['slug']}/generated/AUTHORING.md" in p
    assert f"proposals/matchup--{kid['slug']}.md" in p


def test_a_section_this_issue_does_not_have_is_refused(env):
    """`_section_path` builds a path for any lowercase word, so the prompt
    endpoint asks the issue what it actually contains."""
    client, _db = env
    assert client.get(f"{EDIT}/claude-prompt",
                      params={"section": "no-such-section"}).status_code == 404


def test_the_prompt_carries_paths_not_private_material(env):
    """The privacy guarantee is structural: research stays in files on this
    machine and the clipboard carries only instructions and paths."""
    client, db = env
    with Storage(db) as s:
        kid = matchup_children(s, LG, SEASON, "week-01", 1)[0]
    for section in ("tracks", "lowdown", kid["section"]):
        p = client.get(f"{EDIT}/claude-prompt", params={"section": section}).json()["prompt"]
        assert len(p) < 1200, f"{section}: prompt is long enough to be carrying content"
        for leaked in ("owner_id", "user_id", "sleeper", "roast", "possible move",
                       "private note", "ghost", "evidence:", "take:"):
            assert leaked not in p.lower(), f"{section} leaked {leaked!r}"


def test_the_prompt_endpoint_writes_nothing(env):
    """Copying a prompt is not an editorial act. It must not open a rewrite
    request, touch a section, or move any approval."""
    client, db = env
    client.get(f"{EDIT}/claude-prompt", params={"section": "tracks"})
    with Storage(db) as s:
        assert s.list_rewrite_requests("surfeit", SEASON, "week-01") == []
        assert s.get_issue_modules("surfeit", SEASON, "week-01") == {}
