"""Kill the process between the two halves of a click, then restart.

WHY

Every coupled route in the Desk writes prose and then writes metadata
that describes it, in separate transactions. A happy-path test cannot see
that, because on the happy path both writes land. The question that
decides the cutover is what survives when only the first one does.

The Desk holds no state between requests except its durable job rows, so
a restart is faithfully simulated by discarding the app and building a new
one against the same database and the same editorial tree. That is what
`_restart` does, and every test here asserts on the RESTARTED Desk -- not
on the one that crashed, which could plausibly be blamed for anything.

WHAT THESE PROVE

Three of these leave the Desk asserting something false about the
Commissioner's own writing. One of them -- provenance -- is already built
so that a half-completed click claims nothing rather than claiming
something wrong, and that test is here to pin the pattern the others
should copy.

None of this is hypothetical after a cutover: today both writes go to the
same SQLite file and a crash between them is a narrow window on one
machine. Once prose is in Postgres and the metadata is not, the window is
every network partition between two services.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import prose_store
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, save_section

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
EDIT = f"{BASE}/edit"
# The exact marker, not a paraphrase: origin is settled by an equality
# test against it, and "ROUGH DRAFT" alone settles nothing.
MARK = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"


class Crash(RuntimeError):
    """The process died here."""


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
    db = tmp_path / "crash.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete",
                        season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={r: 90.0 + r for r in range(1, 11)})
        s.set_meta("current_week", "1")
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nWords.\n",
                                                 encoding="utf-8")
    (idir / "sections").mkdir()
    (idir / "proposals").mkdir()
    _FAULT["method"] = None
    return _restart(db), db, idir


# The fault is a flag rather than a permanent patch, so a "restart" can
# disarm it. A process that died once is replaced by a working one running
# the same code; leaving the fault armed would model a permanently broken
# build, which is a different and much less interesting failure.
_FAULT: dict[str, str | None] = {"method": None}


def _restart(db) -> TestClient:
    """A new process against the same durable state.

    raise_server_exceptions=False so an injected crash arrives as a 500,
    the way it would reach a browser, instead of unwinding into the test.
    """
    _FAULT["method"] = None
    prose_store.reset_cache()
    return TestClient(create_app(db_path=db), raise_server_exceptions=False)


def _module(db, key="lowdown") -> dict:
    with Storage(db) as s:
        return s.get_issue_modules("surfeit", SEASON, "week-01").get(key) or {}


def _text(client, section="lowdown") -> str:
    return client.get(f"{EDIT}/section-state",
                      params={"section": section}).json().get("text") or ""


def _version(client, section="lowdown") -> str:
    return client.get(f"{EDIT}/section-state",
                      params={"section": section}).json()["version"]


def _approve(client, section="lowdown") -> None:
    r = client.post(f"{EDIT}/approve",
                    json={"section": section, "action": "approve"})
    assert r.status_code == 200, r.text


def _die_in(monkeypatch, method: str) -> None:
    """Make one Storage method the point the process dies at."""
    real = getattr(Storage, method)

    def maybe(*a, **kw):
        if _FAULT["method"] == method:
            raise Crash(method)
        return real(*a, **kw)

    monkeypatch.setattr(Storage, method, maybe)
    _FAULT["method"] = method


# ------------------------------------------------------ the headline defect

def test_a_crash_after_the_prose_write_leaves_an_approval_over_unapproved_text(
        desk, monkeypatch):
    """The failure the whole editor exists to prevent.

    Save commits the prose, then retires the approval in a later
    transaction. Die in between and the restarted Desk shows a green
    approved chip above text nobody has ever approved -- and nothing in
    the Desk notices, because approval is a boolean rather than a
    signature over the text it describes.
    """
    client, db, _idir = desk
    save_section(client, EDIT, "lowdown", "Text he approved.\n")
    _approve(client)
    assert _module(db).get("approved")

    version = _version(client)
    _die_in(monkeypatch, "set_prose_state")
    r = client.post(f"{EDIT}/save",
                    json={"section": "lowdown", "text": "Text nobody approved.\n",
                          "expected_version": version})
    assert r.status_code == 500

    fresh = _restart(db)
    assert _text(fresh) == "Text nobody approved.\n", "the prose write landed"
    assert _module(db).get("approved"), (
        "and the approval survived it -- this is the defect, pinned")
    assert not _module(db).get("approved_sha"), (
        "nothing binds that approval to any particular text, so nothing "
        "can detect the mismatch")


def test_the_same_crash_would_be_caught_if_approval_were_a_signature(desk):
    """Common Tactical Picture already does it correctly, so the fix is
    known rather than invented.

    CTP's approval carries a hash of the previews it publishes. Editing
    one changes the hash, and an approval whose signature no longer
    matches simply is not an approval -- no code has to notice and no
    second write has to succeed. This test shows the mechanism working on
    the section that has it, which is the pattern the others need.
    """
    from leaguepage.issue_builder import ctp_signature

    client, db, _idir = desk
    with Storage(db) as s:
        before = ctp_signature(s, LG, SEASON, "week-01", 1)
    slug = _first_matchup(client, db)
    save_section(client, EDIT, f"matchup:{slug}", "A preview.\n")
    with Storage(db) as s:
        after = ctp_signature(s, LG, SEASON, "week-01", 1)
    assert before != after, (
        "the signature moves with the text, which is what makes a stale "
        "approval self-retiring")


def _first_matchup(client, db) -> str:
    with Storage(db) as s:
        states = s.list_matchup_states("surfeit", SEASON, 1)
    if states:
        return sorted(states)[0]
    html = client.get(f"{BASE}/room").text
    import re
    m = re.search(r'data-section="matchup:([a-z0-9-]+)"', html)
    assert m, "no matchup section on the page"
    return m.group(1)


# --------------------------------------------------------- the other seams

def test_a_crash_after_restore_leaves_the_state_chip_lying(desk, monkeypatch):
    """Restore has the same shape as save and the same seam."""
    client, db, _idir = desk
    save_section(client, EDIT, "lowdown", "One.\n")
    save_section(client, EDIT, "lowdown", "Two.\n")
    rows = client.get(f"{EDIT}/revisions",
                      params={"section": "lowdown"}).json()["revisions"]
    _approve(client)

    _die_in(monkeypatch, "set_prose_state")
    r = client.post(f"{EDIT}/restore",
                    json={"section": "lowdown", "revision_id": rows[0]["id"],
                          "expected_version": _version(client)})
    assert r.status_code == 500

    fresh = _restart(db)
    assert _text(fresh) == "One.\n", "the restore landed"
    assert _module(db).get("approved"), "the approval outlived the text again"


def test_a_crash_after_replace_with_my_copy_leaves_an_ai_claim_on_empty_text(
        desk, monkeypatch):
    """Replace-with-my-copy clears the section and then rewrites
    authorship. Die between and the section is empty while provenance
    still says a machine wrote it."""
    from leaguepage import provenance

    client, db, idir = desk
    # The real sequence: a Claude Code draft arrives on disk carrying the
    # marker, and his first edit through the Desk is what settles origin.
    (idir / "lowdown" / "lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    prose_store.reset_cache()
    save_section(client, EDIT, "lowdown", MARK + "\n\nGenerated, edited.\n")
    with Storage(db) as s:
        row = s.get_prose_provenance("surfeit", SEASON, "week-01", "lowdown")
    assert provenance.origin_of(row) in ("ai", "deterministic"), (
        f"expected an AI origin to have been settled, got {row}")

    # replace-origin writes provenance FIRST, so dying in set_prose_state
    # would prove nothing. The seam is the prose clear landing while the
    # authorship rewrite does not.
    _die_in(monkeypatch, "set_prose_provenance")
    r = client.post(f"{EDIT}/replace-origin",
                    json={"section": "lowdown", "confirm": "yes",
                          "expected_version": _version(client)})
    assert r.status_code == 500

    fresh = _restart(db)
    assert _text(fresh) == "", "the clear landed"
    with Storage(db) as s:
        after = s.get_prose_provenance("surfeit", SEASON, "week-01", "lowdown")
    assert provenance.origin_of(after) in ("ai", "deterministic"), (
        "authorship still describes text that is no longer there")


def test_a_crash_after_accepting_a_proposal_re_offers_the_accepted_proposal(
        desk, monkeypatch):
    """Accept is two prose objects: put the section, delete the proposal.

    There is no transaction across them on the filesystem, and there will
    not be one across two databases either. Dying between leaves the
    accepted text in place AND the proposal still sitting in the queue,
    so the next click offers him a rewrite he already took.
    """
    client, db, idir = desk
    save_section(client, EDIT, "lowdown", "His own words.\n")
    (idir / "proposals" / "lowdown.md").write_text("A proposed rewrite.\n",
                                                   encoding="utf-8")
    _die_in(monkeypatch, "set_prose_state")
    r = client.post(f"{EDIT}/proposal",
                    json={"section": "lowdown", "action": "accept",
                          "expected_version": _version(client)})
    assert r.status_code == 500

    fresh = _restart(db)
    assert _text(fresh).strip() == "A proposed rewrite.", "the accept landed"
    assert (idir / "proposals" / "lowdown.md").exists(), (
        "and the proposal is still offered, because its deletion was a "
        "separate step that never ran")


def test_a_crash_between_the_rewrite_queue_and_its_file_leaves_them_disagreeing(
        desk, monkeypatch):
    """The rewrite queue is a SQLite row and a Markdown file that a local
    Claude Code session reads. They are written one after the other."""
    client, db, idir = desk
    requests_file = idir / "REVISION_REQUESTS.md"

    r = client.post(f"{EDIT}/request-rewrite",
                    json={"section": "lowdown", "note": "first"})
    assert r.status_code == 200
    assert requests_file.exists()

    _die_in(monkeypatch, "list_rewrite_requests")
    r = client.post(f"{EDIT}/request-rewrite",
                    json={"section": "lowdown", "note": "second"})
    assert r.status_code == 500

    _restart(db)
    with Storage(db) as s:
        rows = s.list_rewrite_requests("surfeit", SEASON, "week-01")
    assert len(rows) == 2, "both requests are in the database"
    assert "second" not in requests_file.read_text(encoding="utf-8"), (
        "and the file a Claude Code session reads knows about one of them")


# ------------------------------------------------- the pattern that holds

def test_a_crash_before_provenance_claims_nothing_rather_than_something_false(
        desk, monkeypatch):
    """Provenance is the one piece of this that is already safe.

    It records a hash of the text it describes, so a click that half
    completes leaves provenance SILENT -- origin 'unknown' -- rather than
    describing text that was never written. Unknown is honest. This is
    the model the approval flag should copy, and it is why the fix is a
    signature rather than a distributed transaction.
    """
    from leaguepage import provenance

    client, db, _idir = desk
    save_section(client, EDIT, "lowdown", "First words.\n")

    _die_in(monkeypatch, "set_prose_provenance")
    r = client.post(f"{EDIT}/save",
                    json={"section": "lowdown", "text": "Second words.\n",
                          "expected_version": _version(client)})
    assert r.status_code in (200, 500)

    fresh = _restart(db)
    with Storage(db) as s:
        row = s.get_prose_provenance("surfeit", SEASON, "week-01", "lowdown")
    origin = provenance.origin_of(row)
    text = _text(fresh)
    if row and row.get("generated_sha"):
        from leaguepage.provenance import text_sha
        assert row["generated_sha"] == text_sha(text) or origin == "unknown", (
            "a provenance claim that survives must still describe the "
            "stored text")
    else:
        assert origin == "unknown", (
            "no claim is the right outcome of a half-completed click")


def test_a_refused_write_is_not_a_crash_and_changes_nothing(desk):
    """The control. Optimistic concurrency already gets this right: a
    save that names a stale version writes nothing at all, so none of the
    seams above are even reached."""
    client, db, _idir = desk
    save_section(client, EDIT, "lowdown", "Stored.\n")
    _approve(client)
    before = _module(db)

    r = client.post(f"{EDIT}/save",
                    json={"section": "lowdown", "text": "Stale writer.\n",
                          "expected_version": "fs1:" + "0" * 16})
    assert r.status_code == 409

    fresh = _restart(db)
    assert _text(fresh) == "Stored.\n"
    assert _module(db).get("approved") == before.get("approved")
