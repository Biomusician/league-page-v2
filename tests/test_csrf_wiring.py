"""The token the server has always demanded and nothing ever sent.

`desk.py` has rejected any POST without `x-csrf-token` or a `csrf_token`
field since authentication landed. Not one template rendered the field and
not one fetch sent the header, so turning auth on 403'd every button in the
building — which is the whole of what stood between this code and
authenticated remote authoring.

The fix is central: the token goes in a meta tag, and `static/desk.js`
attaches it to every form submit and every same-origin mutating fetch. These
tests pin the server half and the shape of the client half; the browser half
was exercised by hand against a running Desk.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from leaguepage import auth
from leaguepage.config import STATIC_DIR, get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from season import populate_season

COMMISH = "commish@example.com"
SEASON = "2026"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", COMMISH)
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    db = tmp_path / "d.sqlite3"
    with Storage(db) as s:
        populate_season(s, get_league("disco"), teams=12, weeks_played=1,
                        current_week=1, season=SEASON, seed=5)
        populate_season(s, get_league("surfeit"), teams=10, weeks_played=1,
                        current_week=1, season=SEASON, seed=6)
    return TestClient(create_app(db), follow_redirects=False)


@pytest.fixture
def signed_in(client):
    session = auth.create_session(COMMISH)
    client.cookies.set(auth.SESSION_COOKIE, session)
    return client, auth.read_session(session)


# --------------------------------------------------------------- server

def test_a_post_with_no_token_is_still_refused(signed_in):
    """The gate is not being weakened; it is being fed."""
    client, _ = signed_in
    r = client.post(f"/commissioner/disco/{SEASON}/inbox/reviewed", data={})
    assert r.status_code == 403


def test_the_form_field_satisfies_it(signed_in):
    client, session = signed_in
    r = client.post(f"/commissioner/disco/{SEASON}/inbox/reviewed",
                    data={"csrf_token": session.csrf})
    assert r.status_code != 403


def test_the_header_satisfies_it(signed_in):
    client, session = signed_in
    r = client.post(f"/commissioner/disco/{SEASON}/inbox/reviewed",
                    headers={"x-csrf-token": session.csrf})
    assert r.status_code != 403


def test_a_token_from_a_different_session_does_not(signed_in):
    """Each session mints its own. A token that is merely well-formed is
    not a token that belongs to this browser."""
    client, session = signed_in
    other = auth.read_session(auth.create_session(COMMISH))
    assert other.csrf != session.csrf
    r = client.post(f"/commissioner/disco/{SEASON}/inbox/reviewed",
                    headers={"x-csrf-token": other.csrf})
    assert r.status_code == 403


# --------------------------------------------------------------- the page

def test_every_desk_page_carries_the_token(signed_in):
    client, session = signed_in
    for path in ("/commissioner", "/commissioner/inbox",
                 f"/commissioner/disco/{SEASON}/draft-review"):
        body = client.get(path).text
        m = re.search(r'<meta name="csrf-token" content="([^"]*)">', body)
        assert m, path
        assert m.group(1) == session.csrf, path


def test_the_page_loads_the_script_that_uses_it(signed_in):
    client, _ = signed_in
    assert '/static/desk.js' in client.get("/commissioner").text


def test_the_script_is_reachable_without_a_session(client):
    """The login page loads it too, and it carries no secret: the token
    reaches it from the page's own meta tag."""
    r = client.get("/static/desk.js")
    assert r.status_code == 200
    assert "csrf" in r.text.lower()


def test_the_login_page_still_works_with_no_session(client):
    assert client.get("/login").status_code == 200


# --------------------------------------------------------------- the script

def test_the_script_covers_forms_and_fetch():
    js = (STATIC_DIR / "desk.js").read_text(encoding="utf-8")
    assert 'name = "csrf_token"' in js or 'input.name = "csrf_token"' in js
    assert '"x-csrf-token"' in js
    assert "window.fetch" in js


def test_the_token_is_never_sent_to_a_third_party():
    """A token handed to somebody else is a token given away."""
    js = (STATIC_DIR / "desk.js").read_text(encoding="utf-8")
    assert "sameOrigin" in js
    assert "location.origin" in js


def test_only_mutating_methods_are_stamped():
    js = (STATIC_DIR / "desk.js").read_text(encoding="utf-8")
    assert "MUTATES" in js
    assert "GET" not in re.search(r"var MUTATES = \{[^}]*\}", js).group(0)


# ------------------------------------------------------- the route map

def test_the_private_route_map_is_not_published(signed_in):
    """docs_url and redoc_url only turn off the two HTML viewers. The schema
    itself stayed live, and with auth off -- which is how the Desk actually
    runs on localhost -- it handed the full private route map to anything
    that could reach the port. Checked while SIGNED IN, because a 401 would
    otherwise pass this test while the route still existed."""
    client, _ = signed_in
    assert client.get("/openapi.json").status_code == 404
