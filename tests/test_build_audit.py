"""The last gate before deploy.

It knew seventeen literal strings, looked only at `.html`, and read only
Sleeper handles out of the manager file. Everything below is a class of
private data that would have shipped.
"""
from __future__ import annotations

import json

import pytest

from leaguepage.site_build import _private_handles, audit_output


@pytest.fixture
def out(tmp_path, monkeypatch):
    """No manager file, so these tests measure the pattern scan alone."""
    from leaguepage import config
    monkeypatch.setattr(config, "EDITORIAL_DIR", tmp_path / "no-editorial")
    d = tmp_path / "dist"
    d.mkdir()
    return d


def _write(out, name, body):
    p = out / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# Every credential-shaped fixture below is ASSEMBLED rather than written
# out. A literal one in a tracked file is exactly what the repo audit exists
# to stop, and a synthetic secret still trips a scanner reading the repo.
SUPA = "https://abcdefghij." + "supa" + "base.co/rest/v1"
JWT = "ey" + "Jhb" + "GciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxIn0.sig"
PG = "postgres" + "ql://user:pw@host:5432/league"


@pytest.mark.parametrize("name,body,label", [
    ("a.html", f"<p>{SUPA}</p>", "Supabase project URL"),
    ("b.html", f"<p>{JWT}</p>", "JWT"),
    ("c.html", "<p>write to someone@example.com about it</p>", "email address"),
    ("d.html", "<p>recommended_status: leaning_wrong</p>", "internal field name"),
    ("e.html", f"<p>{PG}</p>", "database URL"),
    ("f.html", "<p>/Users/somebody/League-Page/notes.txt</p>", "absolute path"),
    ("g.html", "<p>editorial/2026/disco/draft/notes.md</p>", "private repo path"),
    ("h.html", "<p>see PREP.md for the brief</p>", "authoring artifact"),
    # assembled rather than written out: a literal token in a tracked file
    # is exactly what the repo audit exists to stop, even a fake one
    ("i.html", "<p>" + "gh" + "p_" + "a" * 30 + "</p>", "GitHub token"),
])
def test_each_private_shape_is_caught(out, name, body, label):
    _write(out, name, body)
    found = audit_output(out, public_names=[])
    assert any(label in v for v in found), (name, found)


def test_a_finding_never_quotes_the_value(out):
    """An audit report that prints the secret has published it again."""
    _write(out, "a.html", f"<p>{JWT}</p>")
    for v in audit_output(out, public_names=[]):
        assert JWT[:3] not in v, v


def test_non_html_output_is_scanned_too(out):
    """`static/` is copied into the build verbatim, and the audit only ever
    looked at .html."""
    _write(out, "assets/feed.json", json.dumps({"note": "ghost_brief text"}))
    assert audit_output(out, public_names=[])


def test_ordinary_prose_is_not_flagged(out):
    _write(out, "a.html",
           "<p>The Dude beat Corn-Fed Fatties by nine, which nobody had. "
           "His RB room ranks 2 of 12 and the schedule gets harder from here.</p>")
    assert audit_output(out, public_names=[]) == []


# ------------------------------------------------------------- aliases

def _managers(tmp_path, monkeypatch, data):
    from leaguepage import config
    (tmp_path / "managers.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "EDITORIAL_DIR", tmp_path)
    return tmp_path


def test_aliases_are_scanned_when_the_public_names_are_known(tmp_path, monkeypatch):
    """Aliases are where real first names live, and they were invisible."""
    _managers(tmp_path, monkeypatch, {
        "handleone": {"display_name": "handleone",
                      "aliases": ["Bartholomew", "Nickname"]}})
    handles = _private_handles(["Some Team (Nickname)"])
    assert "Bartholomew" in handles


def test_a_nickname_he_published_himself_is_not_private(tmp_path, monkeypatch):
    """Managers put their own nicknames in their team names, so scanning
    aliases without subtracting the public names flags a clean build."""
    _managers(tmp_path, monkeypatch, {
        "handleone": {"display_name": "handleone",
                      "aliases": ["Nickname", "long-team-name"]}})
    handles = _private_handles(["Some Team (Nickname)", "Long Team Name (X)"])
    assert "Nickname" not in handles
    assert "long-team-name" not in handles, "the slug form is in every URL"


def test_without_public_names_only_handles_are_scanned(tmp_path, monkeypatch):
    """A caller that cannot say what is public gets the behaviour that has
    always shipped, rather than a build that fails on its own team names."""
    _managers(tmp_path, monkeypatch, {
        "handleone": {"display_name": "handleone", "aliases": ["Bartholomew"]}})
    assert _private_handles() == ["handleone"]


def test_the_commissioners_own_nickname_is_a_byline_not_a_leak(tmp_path, monkeypatch):
    """He signs the paper. "the commish" in his own approved prose is a
    byline; his Sleeper handle stays private like everyone's, and another
    manager's alias is still somebody else's name."""
    from leaguepage.config import LEAGUES

    lg = LEAGUES[0]
    _managers(tmp_path, monkeypatch, {
        "authorhandle": {"display_name": "authorhandle", "aliases": ["The Commish"],
                         "leagues": {lg.slug: {"roster_id": lg.author_roster_id}}},
        "otherhandle": {"display_name": "otherhandle", "aliases": ["Bartholomew"],
                        "leagues": {lg.slug: {"roster_id": lg.author_roster_id + 1}}}})
    handles = _private_handles(["Some Team (X)"])
    assert "The Commish" not in handles
    assert "authorhandle" in handles and "Bartholomew" in handles
