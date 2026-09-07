"""Approving a matchup from the Whole-Issue Editor.

This blocked Week 1. The editor's Approve chip called
`Storage.set_matchup_state` with four positional arguments; the method is
keyword-only, so every click raised TypeError inside the request and the
endpoint answered 500. It had never worked for any matchup.

The Commissioner experienced it as a dead button rather than an error,
because the handler read `r.json()` before checking `r.ok` — and a 500
returns "Internal Server Error", which is not JSON, so the parse threw and
the alert on the next line never ran.

Nothing about angles was involved. The call died before any angle,
readiness or inclusion logic. These tests pin that: approval works with no
angle selected, at every prominence, and survives a reload.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league

SEASON = "2027"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"
SLUG = "team-1-vs-team-2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
    mdir = ed / SEASON / LG.slug / "week-01" / "matchups" / SLUG
    mdir.mkdir(parents=True)
    (mdir / "draft.md").write_text("Real prose about this matchup.\n", encoding="utf-8")
    return TestClient(create_app(db_path=db)), db


def _approve(client, slug=SLUG, action="approve"):
    return client.post(f"{EDIT}/approve",
                       json={"section": f"matchup:{slug}", "action": action})


def test_a_matchup_approves_from_the_editor(env):
    """The regression itself: this answered 500 for every matchup."""
    client, db = env
    r = _approve(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "approved": True}


def test_the_endpoint_always_answers_json_so_the_button_can_report_failure(env):
    """A non-JSON body is what made a broken endpoint look like a dead
    button: the handler parsed before checking the status."""
    client, _db = env
    for payload in ({"section": "matchup:" + SLUG, "action": "approve"},
                    {"section": "matchup:" + SLUG, "action": "nonsense"},
                    {"section": "matchup:no-such-matchup", "action": "approve"}):
        r = client.post(f"{EDIT}/approve", json=payload)
        assert r.headers["content-type"].startswith("application/json"), payload
        r.json()          # must parse, whatever the status


def test_approval_needs_no_angle_and_records_none(env):
    """Angle selection is not a precondition for approval, and approving
    must not invent one."""
    client, db = env
    assert _approve(client).status_code == 200
    with Storage(db) as s:
        st = s.get_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                                 matchup_slug=SLUG)
    assert st["status"] == "approved"
    assert st["selected_angle_id"] is None
    assert st["custom_angle"] is None


def test_approval_persists_across_a_reload(env):
    client, db = env
    assert _approve(client).status_code == 200
    page = client.get(EDIT)
    assert page.status_code == 200
    with Storage(db) as s:
        assert s.get_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                                   matchup_slug=SLUG)["status"] == "approved"


def test_unapprove_reopens_it(env):
    client, db = env
    _approve(client)
    r = _approve(client, action="unapprove")
    assert r.status_code == 200 and r.json()["approved"] is False
    with Storage(db) as s:
        assert s.get_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                                   matchup_slug=SLUG)["status"] == "edited"


@pytest.mark.parametrize("prominence", ["FEATURE", "MAJOR", "STANDARD"])
def test_every_prominence_approves_without_an_angle(env, prominence):
    """Prominence is an editorial weight, never an approval gate."""
    client, db = env
    with Storage(db) as s:
        s.set_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                            matchup_slug=SLUG, prominence_override=prominence)
    assert _approve(client).status_code == 200
    with Storage(db) as s:
        st = s.get_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                                 matchup_slug=SLUG)
    assert st["status"] == "approved"
    assert st["prominence_override"] == prominence
    assert st["selected_angle_id"] is None


def test_storage_call_uses_keywords(env):
    """The root cause was a positional call to a keyword-only method. If
    someone reintroduces one anywhere, this catches it before a user does."""
    import ast
    import inspect
    import pathlib

    from leaguepage import storage as storage_mod

    kwonly = {
        name for name, fn in vars(Storage).items()
        if callable(fn) and not name.startswith("__")
        and (lambda sig: not [p for p in sig.parameters.values()
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                              and p.name != "self"]
             and any(p.kind == p.KEYWORD_ONLY for p in sig.parameters.values()))(
            inspect.signature(fn))
    }
    assert "set_matchup_state" in kwonly, "the method under test is no longer keyword-only"
    root = pathlib.Path(storage_mod.__file__).parent
    offenders = []
    for f in root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in kwonly and node.args):
                offenders.append(f"{f.name}:{node.lineno} {node.func.attr}()")
    assert offenders == [], offenders


def test_a_port_method_sharing_a_storage_name_shares_its_convention():
    """Why the check above keeps catching the editorial state port.

    `EditorialState` deliberately mirrors several Storage method names,
    because they mean the same thing. The AST walk cannot tell the two
    apart -- it sees an attribute name -- so a port method that took
    those arguments positionally would make the guard fire on correct
    code, and the tempting fix would be to teach the guard an exception.

    That would be the wrong fix. The reason Storage made them keyword-only
    is that they are runs of interchangeable strings, and that reason
    holds on the port too. So the rule is the simple one: same name, same
    convention.
    """
    import inspect

    from leaguepage import editorial_state as est
    from leaguepage.storage import Storage

    def no_positional_data(fn) -> bool:
        """Nothing meaningful can be passed to it by position.

        `**fields` counts: a method that takes only keyword arguments
        cannot be called the wrong way round, which is the whole point.
        """
        sig = inspect.signature(fn)
        return not [p for p in sig.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                                  p.VAR_POSITIONAL)
                    and p.name != "self"]

    def kwonly(fn) -> bool:
        return (no_positional_data(fn)
                and any(p.kind == p.KEYWORD_ONLY
                        for p in inspect.signature(fn).parameters.values()))

    shared = []
    for cls in (est.SqliteEditorialState, est.PostgresEditorialState,
                est.EditorialState):
        for name, fn in vars(cls).items():
            if name.startswith("_") or not callable(fn):
                continue
            storage_fn = getattr(Storage, name, None)
            if storage_fn is None or not kwonly(storage_fn):
                continue
            shared.append(f"{cls.__name__}.{name}")
            assert no_positional_data(fn), (
                f"{cls.__name__}.{name} shares a keyword-only Storage name "
                "but takes positional arguments. Make it keyword-only, or "
                "give it a different name -- do not teach the AST guard an "
                "exception.")
    assert shared, ("no port method shares a keyword-only Storage name any "
                    "more; if that is deliberate, delete this test")
