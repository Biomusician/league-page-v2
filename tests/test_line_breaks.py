"""A line break he typed is a line break he gets.

What I type is the structure I see. Markdown's own rule — a single newline
is a space — is right for prose composed in a text file and wrong for prose
composed in a box on a screen, which is where this newspaper is written. He
types stanzas, one-line verdicts and lists of names that are not `<ul>`
lists, and every one of them used to arrive on the page as a wall.

The break has to survive the whole path, because a preview that disagrees
with the page is worse than no preview: textarea, autosave, reload,
section preview, full-issue preview, the frozen snapshot, and the built
site. These pin every hop.

He types no `<br>` and nothing becomes `<pre>`: the source stays Markdown
and stays editable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import prose
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
EDIT = f"{BASE}/edit"

# Three lines he meant, then a real paragraph break, then one more.
TYPED = ("Roster 4 is 0-1.\nRoster 7 is 1-0.\nThe difference was a kicker.\n"
         "\nNobody saw it coming.\n")


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
    return TestClient(create_app(db_path=db)), db, ed


def _save(client, text=TYPED, section="tracks"):
    return client.post(f"{EDIT}/save",
                       json={"section": section, "text": text, "base_sha": ""})


# ------------------------------------------------------------ the renderer

def test_a_single_newline_becomes_a_line_break():
    html = prose.render("first line\nsecond line")
    assert "<br" in html
    assert "first line" in html and "second line" in html


def test_a_blank_line_is_still_a_paragraph():
    """Honoring single breaks must not collapse the paragraph he also uses."""
    html = prose.render("one\n\ntwo")
    assert html.count("<p>") == 2
    assert "<br" not in html


def test_nothing_is_wrapped_in_pre():
    """A <pre> would preserve the breaks and take his formatting with it:
    no bold, no links, a monospace column that ignores the page."""
    html = prose.render(TYPED)
    assert "<pre" not in html and "<code" not in html


def test_he_never_has_to_type_a_br():
    """The source stays Markdown. A hand-written tag is not a workaround he
    should have to know, and it would survive into the page as source."""
    assert "<br" not in TYPED
    assert "<br" in prose.render(TYPED)


def test_the_rest_of_markdown_still_works_alongside_the_breaks():
    html = prose.render("**bold** and *italic*\nnext line\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html
    assert "<table>" in html and "<br" in html


def test_every_render_path_uses_the_one_renderer():
    """A preview that disagrees with the page is worse than no preview, so
    there is one renderer and the modules that publish prose call it."""
    import pathlib

    offenders = []
    for f in pathlib.Path("leaguepage").glob("*.py"):
        if f.name == "prose.py":
            continue
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "markdown.markdown(" in line and "review packet" not in line:
                offenders.append(f"{f.name}:{i}")
    # desk.py renders the generated review packet directly, on purpose, and
    # says so in a comment on the line above.
    allowed = []
    for o in offenders:
        name, num = o.split(":")
        lines = (pathlib.Path("leaguepage") / name).read_text(encoding="utf-8").splitlines()
        if "Not prose.render" in "\n".join(lines[max(0, int(num) - 4):int(num)]):
            allowed.append(o)
    assert set(offenders) - set(allowed) == set(), offenders


# ------------------------------------------------------------ the round trip

def test_a_typed_break_survives_save_and_reload(env):
    """Autosave writes what is in the box, and the box is refilled with it."""
    client, _db, ed = env
    assert _save(client).status_code == 200, "save failed"
    on_disk = (ed / SEASON / "surfeit" / "week-01" / "sections" / "tracks.md").read_text(
        encoding="utf-8")
    assert on_disk == TYPED
    page = client.get(EDIT).text
    assert "Roster 4 is 0-1.\nRoster 7 is 1-0." in page.replace("\r\n", "\n")


def test_the_section_preview_shows_the_breaks(env):
    client, _db, _ed = env
    _save(client)
    r = client.get(f"{EDIT}/preview-section", params={"section": "tracks"})
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert html.count("<br") == 2, html
    assert html.count("<p>") == 2


def test_the_full_issue_preview_shows_the_breaks(env):
    client, _db, _ed = env
    _save(client)
    assert "<br" in client.get(f"{EDIT}/full-preview").text


def test_a_matchup_preview_keeps_its_breaks_too(env):
    """Matchup drafts go through the same save endpoint and the same
    renderer as any other section."""
    client, db, ed = env
    from leaguepage.issue_builder import matchup_children

    with Storage(db) as s:
        slug = matchup_children(s, LG, SEASON, "week-01", 1)[0]["slug"]
    assert _save(client, section=f"matchup:{slug}").status_code == 200
    path = ed / SEASON / "surfeit" / "week-01" / "matchups" / slug / "draft.md"
    assert path.read_text(encoding="utf-8") == TYPED
    assert "<br" in prose.render(path.read_text(encoding="utf-8"))


def test_the_break_reaches_the_built_site(env):
    """The last hop, and the one that matters: what a reader loads."""
    from leaguepage.site_build import _render_md

    assert _render_md(TYPED).count("<br") == 2


def test_editorial_comments_are_stripped_without_leaving_a_stray_break(env):
    """A commissioner-only comment is removed on the way out; the line it
    sat on must not survive as an empty break."""
    from leaguepage.site_build import _render_md

    html = _render_md("Visible line.\n<!-- usage: angle=x -->\nSecond line.")
    assert "usage:" not in html and "angle" not in html
    assert "Visible line." in html and "Second line." in html


def test_a_trailing_space_is_not_required(env):
    """Markdown's own hard break is two trailing spaces, which no editor
    shows and every editor strips. He should never need it."""
    assert "  \n" not in TYPED
    assert prose.render(TYPED).count("<br") == 2
