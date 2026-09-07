"""The prose routes, asked whether they still own a transaction.

Tranche 5C section 6 is explicit: do not mark a route moved because it
calls EditorialStore somewhere. So this file asks two separate questions
and requires both answers.

STRUCTURAL -- does the route body still perform an authoritative write of
its own? A route that calls the store AND writes provenance beside it is
worse than one that never moved, because the route audit would read green
while two owners went on committing separately.

BEHAVIOURAL -- drive the route with a fault at the metadata seam and look
at what survived. Everything describing one prose write is inside one
transaction, so breaking any part of it must leave NONE of it. What the
filesystem cannot do is join that to the prose write itself: the words
survive with nothing describing them, which is honest, and is the best
two owners can manage. Postgres closes that last gap and
`test_editorial_store.py` proves it against the live database; this file
proves the routes are shaped to benefit.
"""
from __future__ import annotations

import inspect
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import editorial_state as est
from leaguepage import prose_store
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, save_section

SEASON = "2027"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"
MARK = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"
# The marker settles origin wherever it appears, but it is only STRIPPED
# on a comment line of its own: accepted prose is never silently edited.
MARK_LINE = f"<!-- {MARK} -->"
SLUG = "team-1-vs-team-2"

# Every route that has given up its own transaction. PARTIAL exists so a
# half-migrated route can be recorded as half migrated rather than going
# unmentioned, which is exactly how an audit starts lying; it is empty
# because nothing is currently in that state, not because the idea was
# abandoned.
MOVED = (
    # prose and what describes it
    "editor_save", "editor_restore", "editor_reset_generated",
    "editor_replace_origin", "proposal_action", "matchup_draft_save",
    "lowdown_save", "qa_action",
    # claims about writing, and the stages around them
    "editor_approve", "editor_module", "editor_custom", "editor_rankings",
    "issue_module_update", "set_theme", "rankings_save", "save_power",
    "matchup_angle", "matchup_prominence", "matchup_revision",
    "matchup_status_change",
    # what he decided, and about whom
    "story_decide", "decide_story", "inbox_decide", "award_decide",
    "decide_award", "false_assumption_decide",
    # the ledger, the names, the queue and the baseline
    "track_take", "add_take", "take_action", "resolve_take",
    "set_team_names", "use_sleeper_name", "request_rewrite",
    "force_flow_note", "inbox_reviewed",
)
PARTIAL: dict[str, str] = {}

# Substrings that mean "this route writes something authoritative itself".
# Everything reached through `act.` is the store's write, not the route's.
FORBIDDEN = {
    "s.transaction()": "opens a transaction of its own",
    "provenance.record(": "writes provenance itself",
    "provenance.mark_commissioner(": "writes provenance itself",
    "provenance.note_assistance(": "writes provenance itself",
    "s.set_prose_state(": "writes prose state itself",
    "s.set_prose_provenance(": "writes provenance itself",
    "s.set_prose_assistance(": "writes provenance itself",
    "s.set_matchup_state(": "writes matchup state itself",
    "s.set_issue_module(": "writes the approval itself",
    "s.set_issue_theme(": "writes the issue row itself",
    "s.set_story_decision(": "writes a story decision itself",
    "s.set_award_decision(": "writes an award decision itself",
    "s.save_power_rankings(": "writes the rankings itself",
    "s.log_editorial_usage(": "writes the repetition log itself",
    "provenance.note_rankings(": "writes provenance itself",
    "repo.put(": "writes prose outside the action",
    "repo.delete(": "deletes prose outside the action",
    "_repo().put(": "writes prose outside the action",
    "_repo().delete(": "deletes prose outside the action",
}


def _route_sources() -> dict[str, str]:
    app = create_app(db_path=":memory:")
    out = {}
    for r in app.routes:
        fn = getattr(r, "endpoint", None)
        if fn is None:
            continue
        try:
            out[r.name] = inspect.getsource(fn)
        except (OSError, TypeError):        # pragma: no cover - defensive
            continue
    return out


# ------------------------------------------------------------ structural

@pytest.mark.parametrize("name", MOVED)
def test_a_moved_route_writes_nothing_on_its_own(name):
    src = _route_sources()[name]
    guilty = [why for token, why in FORBIDDEN.items() if token in src]
    assert not guilty, f"{name} still {guilty}"


@pytest.mark.parametrize("name", MOVED)
def test_a_moved_route_goes_through_the_store(name):
    src = _route_sources()[name]
    assert "_editorial().action(" in src, (
        f"{name} performs no editorial action; if it genuinely writes "
        "nothing it does not belong in MOVED")


def test_every_authoring_route_is_accounted_for():
    """Nothing authoring is quietly outside this file.

    A route that never appears in MOVED or PARTIAL is not "fine", it is
    unexamined, and unexamined is how the first cutover analysis came to
    describe a fifth of the surface.
    """
    from test_hosted_mutation_audit import CLAIMS

    authoring = {n for n, c in CLAIMS.items() if c.kind == "authoring"}
    unexamined = sorted(authoring - set(MOVED) - set(PARTIAL))
    assert not unexamined, f"authoring route(s) never examined: {unexamined}"
    assert not (set(MOVED) | set(PARTIAL)) - authoring, "a name that is not a route"


def test_store_owned_is_not_the_same_claim_as_hosted_safe():
    """The distinction the whole tranche turns on.

    Every authoring route now expresses intent to the store instead of
    holding a transaction. That is the shape a cutover needs and it is
    not a cutover: the backend setting has not moved, no data has been
    imported, and the writes still land on this machine. If this test
    ever fails because a route claims hosted safety, the thing to do is
    read the cutover gate, not delete this.
    """
    from test_hosted_mutation_audit import CLAIMS

    for name in MOVED:
        assert not CLAIMS[name].safe, (
            f"{name} claims hosted safety merely for having moved")


def test_a_half_migrated_route_is_recorded_as_half_migrated():
    """The check that stops this file from flattering itself.

    A route that calls the store on one branch and writes directly on
    another has not moved, and the dangerous version of that is the one
    nobody wrote down. PARTIAL is where it goes; anything in it must
    really still write for itself, or it belongs in MOVED.
    """
    assert set(PARTIAL) & set(MOVED) == set(), "a route is one or the other"
    for name in PARTIAL:
        src = _route_sources()[name]
        assert any(token in src for token in FORBIDDEN), (
            f"{name} writes nothing of its own any more -- move it into "
            "MOVED rather than leaving it recorded as half migrated")


# ----------------------------------------------------------- behavioural

class Boom(RuntimeError):
    """A seam failing for reasons the route cannot know about."""


@contextmanager
def fault_in(cls, method: str):
    """Break one state method for the duration of one request.

    Deliberately NOT monkeypatch: undoing a monkeypatch undoes every patch
    the fixture made too, including the editorial tree, and a test that
    then reads the real tree is measuring nothing.
    """
    original = getattr(cls, method)

    def boom(*a, **kw):
        raise Boom(method)

    setattr(cls, method, boom)
    try:
        yield
    finally:
        setattr(cls, method, original)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [],
                                 "relationships": []})
    db = tmp_path / "desk.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete",
                        season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={r: 90.0 + r for r in range(1, 11)})
        s.set_meta("current_week", "1")
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "sections").mkdir()
    (idir / "proposals").mkdir()
    prose_store.reset_cache()
    yield TestClient(create_app(db_path=db)), db, idir
    prose_store.reset_cache()


def _provenance(db, section="lowdown"):
    with Storage(db) as s:
        return s.get_prose_provenance("surfeit", SEASON, "week-01", section)


def _state(db, section="lowdown"):
    with Storage(db) as s:
        return s.get_prose_states("surfeit", SEASON, "week-01").get(section)


def test_a_broken_seam_leaves_none_of_the_description(desk):
    """Provenance and prose state travel together or not at all.

    The section arrives carrying the rough-draft marker, so this save
    settles origin AND sets a prose state: two writes that the old route
    committed separately. Breaking the second must lose the first.
    """
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    prose_store.reset_cache()
    assert _provenance(db) is None and _state(db) is None

    with fault_in(est.SqliteEditorialState, "set_prose_state"):
        with pytest.raises(Boom):
            save_section(client, EDIT, "lowdown", "Edited into shape.\n")

    assert _provenance(db) is None, (
        "provenance survived a failure in the same transaction as it")
    assert _state(db) is None
    # And the honest part: two owners, so the words did land.
    assert "Edited into shape." in (idir / "lowdown" / "lowdown.md").read_text(
        encoding="utf-8"), (
        "the filesystem cannot join the prose write to its description; "
        "if this ever fails the backend gained atomicity and the claim "
        "in tests/test_hosted_mutation_audit.py should say so")


def test_prose_with_no_description_claims_nothing(desk):
    """Why the gap above is survivable rather than a bug.

    A section whose provenance write was lost reads as unknown origin,
    which is the same thing the Desk says about text it has never been
    told anything about. It does not read as the Commissioner's, and it
    does not read as approved.
    """
    from leaguepage import provenance

    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    prose_store.reset_cache()
    with fault_in(est.SqliteEditorialState, "set_prose_state"):
        with pytest.raises(Boom):
            save_section(client, EDIT, "lowdown", "Edited into shape.\n")
    assert provenance.origin_of(_provenance(db)) == "unknown"


def test_restore_loses_its_state_write_without_losing_history(desk):
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text("First.\n", encoding="utf-8")
    prose_store.reset_cache()
    assert save_section(client, EDIT, "lowdown", "Second.\n").status_code == 200
    revs = client.get(f"{EDIT}/revisions",
                      params={"section": "lowdown"}).json()
    rid = revs["revisions"][0]["id"]

    with fault_in(est.SqliteEditorialState, "set_prose_state"):
        with pytest.raises(Boom):
            client.post(f"{EDIT}/restore",
                        json={"section": "lowdown", "revision_id": rid})
    # The revision list is history, not a description of the current text,
    # so it is untouched by the failure and the restore can be retried.
    again = client.get(f"{EDIT}/revisions",
                       params={"section": "lowdown"}).json()
    assert [r["id"] for r in again["revisions"]][-1:] == \
           [revs["revisions"][-1]["id"]]


def test_accepting_a_proposal_is_refused_when_the_proposal_moved(desk):
    """The check and the accept are now inside one action.

    Before, the route read the proposal, compared versions, then wrote in
    a separate transaction. Nothing can move in between any more, and a
    stale `proposal_version` is still refused with a conflict rather than
    silently publishing text he never read.
    """
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text("His own words.\n",
                                                 encoding="utf-8")
    (idir / "proposals" / "lowdown.md").write_text("A rewrite.\n",
                                                   encoding="utf-8")
    prose_store.reset_cache()
    r = client.post(f"{EDIT}/proposal",
                    json={"section": "lowdown", "action": "accept",
                          "proposal_version": "not-the-stored-one"})
    assert r.status_code == 409, r.text
    assert (idir / "proposals" / "lowdown.md").exists(), "nothing was retired"
    assert "His own words." in (idir / "lowdown" / "lowdown.md").read_text(
        encoding="utf-8")


def test_accepting_a_proposal_records_who_wrote_it(desk):
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text("His own words.\n",
                                                 encoding="utf-8")
    (idir / "proposals" / "lowdown.md").write_text(
        MARK_LINE + "\n\nA rewrite.\n", encoding="utf-8")
    prose_store.reset_cache()
    r = client.post(f"{EDIT}/proposal",
                    json={"section": "lowdown", "action": "accept"})
    assert r.status_code == 200, r.text
    row = _provenance(db)
    assert row["origin"] == "ai" and row["generator"] == "claude-code"
    assert row["event"] == "proposal-accept"
    assert not (idir / "proposals" / "lowdown.md").exists()
    text = (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    assert MARK not in text, "accepting IS the review; the marker comes off"
    assert "A rewrite." in text, "and the words survive it"


def test_discarding_a_proposal_records_the_help_without_claiming_authorship(desk):
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text("His own words.\n",
                                                 encoding="utf-8")
    (idir / "proposals" / "lowdown.md").write_text("A rewrite.\n",
                                                   encoding="utf-8")
    prose_store.reset_cache()
    r = client.post(f"{EDIT}/proposal",
                    json={"section": "lowdown", "action": "discard"})
    assert r.status_code == 200, r.text
    row = _provenance(db)
    assert row["assistance"] == "ai-writing"
    assert row["origin"] == "unknown", (
        "reading a proposal says nothing about who wrote the section")
    assert not (idir / "proposals" / "lowdown.md").exists()


def test_two_saves_from_the_same_version_and_only_one_wins(desk):
    """Concurrency, through the route rather than the store.

    The second save is refused before anything is written, so it leaves no
    revision, no provenance and no state change -- which is what makes a
    refused save safe to retry from a phone that was a sentence behind.
    """
    client, db, idir = desk
    (idir / "lowdown" / "lowdown.md").write_text("Start.\n", encoding="utf-8")
    prose_store.reset_cache()
    state = client.get(f"{EDIT}/section-state",
                       params={"section": "lowdown"}).json()
    body = {"section": "lowdown", "expected_version": state.get("version")}
    first = client.post(f"{EDIT}/save", json={**body, "text": "Laptop.\n"})
    second = client.post(f"{EDIT}/save", json={**body, "text": "Phone.\n"})
    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert "Laptop." in (idir / "lowdown" / "lowdown.md").read_text(
        encoding="utf-8")


def _matchup_status(db, slug=SLUG):
    with Storage(db) as s:
        return (s.get_matchup_state(league_slug="surfeit", season=SEASON,
                                    week=1, matchup_slug=slug) or {}
                ).get("status") or ""


def test_a_matchup_draft_and_its_stage_move_together(desk):
    """The week page's own form, which used to be two writes in a row.

    The draft is prose and the stage is metadata about that prose, and
    saving one without the other is how a preview comes to sit at
    "approved" over words nobody approved. Breaking the stage write must
    now cost the whole action.
    """
    client, db, idir = desk
    mdir = idir / "matchups" / SLUG
    mdir.mkdir(parents=True)
    (mdir / "draft.md").write_text("Before.\n", encoding="utf-8")
    prose_store.reset_cache()
    url = f"/commissioner/surfeit/{SEASON}/week/1/matchups/{SLUG}/draft"

    with fault_in(est.SqliteEditorialState, "set_matchup"):
        with pytest.raises(Boom):
            client.post(url, data={"draft_text": "After.\n"})
    assert _matchup_status(db) != "edited", (
        "the stage moved even though the action failed")

    r = client.post(url, data={"draft_text": "After.\n"}, follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    assert _matchup_status(db) == "edited"
    assert "After." in (mdir / "draft.md").read_text(encoding="utf-8")
