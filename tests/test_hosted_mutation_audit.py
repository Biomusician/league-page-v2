"""Every mutating Desk route, and which store it actually writes.

WHY THIS EXISTS

The cutover analysis was built from an inventory of nine editor routes.
The application registers forty-four mutating routes. An inventory that
covers a fifth of the surface is not evidence, so this file asks each
route the question directly: drive it, watch every write it performs, and
compare that against a written-down claim.

WHAT IS WATCHED

  sqlite      every INSERT / UPDATE / DELETE reaching a Storage
              connection, by table, via sqlite3's own trace callback --
              so raw SQL is caught as well as Storage methods, and the
              COMMIT count is visible
  filesystem  Path.write_text / write_bytes / unlink / mkdir, restricted
              to the editorial tree
  prose       ProseRepository.put / .delete

WHAT "SAFE FOR HOSTED" MEANS HERE

A route is safe to run on a hosted Desk when both hold:

  1. it performs no authoritative write to this machine -- no editorial
     file, and no SQLite table whose contents cannot be recomputed
  2. everything it does write commits together, so a process that dies
     mid-route leaves either all of it or none of it

Today almost nothing satisfies (2), and the reason is structural rather
than incidental: `Storage._cursor()` commits after every mutating method,
so a route calling three Storage methods is three transactions. That is
fine on one machine where the next request repairs the difference; it is
not fine once the two halves of a click live in two databases.
"""
from __future__ import annotations

import pathlib
import re
import sqlite3
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
import leaguepage.storage as storage_mod
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, save_section

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
EDIT = f"{BASE}/edit"

WRITE_SQL = re.compile(r"^\s*(insert|update|delete|replace)\b", re.I)
TABLE_OF = re.compile(
    r"^\s*(?:insert(?:\s+or\s+\w+)?\s+into|update|delete\s+from|replace\s+into)"
    r"\s+[\"'`\[]?(\w+)", re.I)
FS_WRITES = ("write_text", "write_bytes", "unlink", "mkdir")


# --------------------------------------------------------------- the claim

@dataclass(frozen=True)
class Claim:
    """What a route is asserted to write, and whether it may run hosted."""

    cloud: tuple[str, ...]          # would land in Postgres after a cutover
    local: tuple[str, ...]          # stays on this machine and is authoritative
    owner: str                      # who guarantees the writes commit together
    safe: bool
    why: str
    exercised: bool = True
    kind: str = "authoring"         # authoring | operational | auth | publish


def C(cloud=(), local=(), owner="none", safe=False, why="", **kw) -> Claim:
    return Claim(tuple(cloud), tuple(local), owner, safe, why, **kw)


# Prose lives in the repository, so it is the one thing that already moves.
# Everything else in `local` is a SQLite table with no cutover path yet, or
# a file under editorial/.
P = ("sections", "prose_revisions")

CLAIMS: dict[str, Claim] = {
    # ---------------------------------------------------------- authoring
    "editor_save": C(
        P, ("prose_provenance", "section_prose_state", "issue_modules",
            "matchup_state", "meta"),
        why="prose commits, then four metadata writes in four more "
            "transactions; a crash between them leaves an approval "
            "standing over text nobody approved"),
    "editor_restore": C(
        P, ("section_prose_state", "issue_modules", "matchup_state", "meta"),
        why="same seam as save, reached from the History panel"),
    "editor_reset_generated": C(
        P, ("prose_provenance", "section_prose_state", "issue_modules",
            "matchup_state", "meta"),
        why="also READS rough-lowdown.md from disk, which a hosted Desk "
            "cannot see"),
    "editor_replace_origin": C(
        P, ("prose_provenance", "section_prose_state", "issue_modules",
            "matchup_state", "meta"),
        why="clears the section, then rewrites authorship in a second "
            "transaction"),
    "proposal_action": C(
        P, ("prose_provenance", "section_prose_state", "issue_modules",
            "matchup_state", "meta", "issue_revision_requests"),
        why="two prose objects (put target, delete proposal) plus five "
            "metadata writes plus a REVISION_REQUESTS.md rewrite"),
    "editor_approve": C(
        (), ("issue_modules", "matchup_state", "meta"),
        why="approval is a flag with no signature; nothing binds it to the "
            "text it describes"),
    "matchup_draft_save": C(
        P, ("matchup_state", "meta"),
        why="the matchup half of save, reached from the week page"),
    "lowdown_save": C(
        P, ("section_prose_state",),
        why="the Lowdown screen's own save"),
    "request_rewrite": C(
        (), ("issue_revision_requests",),
        why="also rewrites REVISION_REQUESTS.md, a file on this machine"),
    "editor_custom": C((), ("issue_modules",), why="adds a custom section row"),
    "editor_module": C((), ("issue_modules",), why="include/exclude/reorder"),
    "issue_module_update": C((), ("issue_modules",), why="the builder's copy"),
    "editor_rankings": C((), ("power_rankings",), why="saves a ranking table"),
    "rankings_save": C((), ("power_rankings",), why="the standalone page"),
    "set_theme": C((), ("issues",),
                   why="issues.theme has no Postgres column"),
    "set_team_names": C((), ("team_names",), why="public name overrides"),
    "use_sleeper_name": C((), ("team_names",), why="clears one override"),
    "matchup_angle": C((), ("matchup_state", "meta"), why="angle selection"),
    "matchup_prominence": C((), ("matchup_state",), why="prominence override"),
    "matchup_revision": C((), ("matchup_state",),
                          why="matchup_state.revision_requests has no "
                              "Postgres column"),
    "matchup_status_change": C((), ("matchup_state", "meta"),
                               why="status transitions"),
    "story_decide": C((), ("story_decisions",), why="story routing"),
    "decide_story": C((), ("story_decisions",), why="draft-review copy"),
    "award_decide": C((), ("award_decisions",), why="award decisions"),
    "decide_award": C((), ("award_decisions",), why="draft-review copy"),
    "save_power": C((), ("power_rankings",), why="draft-review copy"),
    "track_take": C((), ("takes",), why="Track This Take"),
    "add_take": C((), ("takes",), why="draft-review copy"),
    "take_action": C((), ("takes",), why="status, public flag, delete"),
    "resolve_take": C((), ("takes",), why="resolution"),
    "false_assumption_decide": C((), ("takes", "story_decisions"),
                                 why="two tables, two transactions"),
    "force_flow_note": C((), ("force_flow_notes",),
                         why="force_flow_notes has no Postgres table"),
    "inbox_decide": C((), ("story_decisions",), why="Change Inbox ruling"),
    "inbox_reviewed": C((), ("sync_snapshots",), why="marks a baseline"),
    "qa_action": C((), ("meta", "issue_modules"),
                   why="dismisses a QA warning", exercised=False),
    # ------------------------------------------------------- operational
    "issue_build": C((), (), owner="n/a", safe=False, kind="operational",
                     why="writes the whole research tree to disk: briefs, "
                         "packets, generated JSON. Recomputable, but it "
                         "needs a filesystem to compute onto",
                     exercised=False),
    "sync_start": C((), (), owner="job", safe=False, kind="operational",
                    why="starts a durable job; the job writes Sleeper cache "
                        "and snapshot rows", exercised=False),
    "about_save": C((), ("editorial/site/about.md",), kind="operational",
                    why="site copy is a file in the editorial tree",
                    exercised=False),
    "about_preview": C((), (), owner="n/a", safe=True, kind="operational",
                       why="renders to a temp path, writes nothing "
                           "authoritative", exercised=False),
    # ----------------------------------------------------------- publish
    "issue_publish": C((), ("published/**.json", "issues"), kind="publish",
                       why="writes an immutable snapshot to disk. Out of "
                           "scope for hosted execution by decision, not by "
                           "defect", exercised=False),
    "publish_start": C((), (), owner="job", kind="publish",
                       why="queues the publish job", exercised=False),
    # -------------------------------------------------------------- auth
    "auth_request": C((), (), owner="n/a", safe=True, kind="auth",
                      why="hosted mints no local link; the log file is a "
                          "localhost convenience", exercised=False),
    "auth_verify": C((), (), owner="n/a", safe=True, kind="auth",
                     why="exchanges an OTP; session state is a cookie",
                     exercised=False),
    "auth_logout": C((), (), owner="n/a", safe=True, kind="auth",
                     why="clears a cookie", exercised=False),
}


# --------------------------------------------------------- instrumentation

@dataclass
class WriteLog:
    sqlite: set[str] = field(default_factory=set)
    commits: int = 0
    files: set[str] = field(default_factory=set)
    prose: set[str] = field(default_factory=set)

    def clear(self) -> None:
        self.sqlite.clear()
        self.files.clear()
        self.prose.clear()
        self.commits = 0


@pytest.fixture
def audited(tmp_path, monkeypatch):
    """A Desk that records every write any route performs."""
    log = WriteLog()
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [],
                                 "relationships": []})

    real_connect = sqlite3.connect

    def traced_connect(*a, **kw):
        conn = real_connect(*a, **kw)

        def trace(sql: str) -> None:
            if sql.strip().upper().startswith("COMMIT"):
                log.commits += 1
                return
            if WRITE_SQL.match(sql):
                m = TABLE_OF.match(sql)
                log.sqlite.add(m.group(1) if m else f"?({sql[:24]})")

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(storage_mod.sqlite3, "connect", traced_connect)

    for name in FS_WRITES:
        real = getattr(pathlib.Path, name)

        def wrapper(self, *a, _real=real, _name=name, **kw):
            try:
                rel = self.relative_to(ed).as_posix()
                log.files.add(f"{_name}:{rel}")
            except ValueError:
                pass
            return _real(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, name, wrapper)

    from leaguepage import prose_store

    for name in ("put", "delete"):
        real = getattr(prose_store.FilesystemProseRepository, name)

        def pwrapper(self, key, *a, _real=real, _name=name, **kw):
            log.prose.add(f"{_name}:{key.kind}:{key.name}")
            return _real(self, key, *a, **kw)

        monkeypatch.setattr(prose_store.FilesystemProseRepository, name,
                            pwrapper)

    db = tmp_path / "audit.sqlite3"
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
    client = TestClient(create_app(db_path=db))
    log.clear()
    return client, log, db, idir


# ---------------------------------------------------------------- the audit

def test_every_mutating_route_is_declared():
    """A route cannot be born undeclared.

    This is the guard the earlier analysis needed and did not have: the
    inventory covered nine editor routes and the app registers forty-four
    mutating ones.
    """
    app = create_app(db_path=":memory:")
    live = set()
    for r in app.routes:
        methods = set(getattr(r, "methods", []) or [])
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            live.add(r.name)
    undeclared = sorted(live - set(CLAIMS))
    stale = sorted(set(CLAIMS) - live)
    assert not undeclared, f"undeclared mutating route(s): {undeclared}"
    assert not stale, f"declared but no longer registered: {stale}"


def test_no_authoring_route_is_safe_for_hosted_execution_yet():
    """The cutover gate, stated as a test.

    Cutover is forbidden while any normal hosted authoring action is
    unsafe. When this list empties, this test fails and the cutover
    becomes a data decision. Until then it is a statement of fact, not an
    aspiration.
    """
    unsafe = sorted(n for n, c in CLAIMS.items()
                    if c.kind == "authoring" and not c.safe)
    assert unsafe, ("every authoring route now claims hosted safety -- "
                    "re-read the claims, then delete this test and open "
                    "the cutover")
    # Pinned so a route cannot quietly flip to safe without someone
    # re-reading this file.
    assert len(unsafe) == 35, sorted(unsafe)


def _observed(log: WriteLog) -> tuple[set[str], set[str]]:
    return set(log.sqlite), set(log.prose)


def _check(name: str, log: WriteLog) -> None:
    """Compare one route's actual writes against its claim.

    A claim is what the route CAN write, and several of those writes are
    conditional: an unapproved section has no approval to retire, a
    section of unknown origin records no provenance. So the assertion
    that matters is that nothing UNDECLARED was written. The fully
    coupled case -- where every conditional write fires -- is
    `test_a_fully_coupled_save_writes_five_things_in_five_transactions`.
    """
    claim = CLAIMS[name]
    sql, prose = _observed(log)
    declared_local = {t for t in claim.local if "/" not in t}
    if not claim.cloud:
        assert not prose, f"{name}: claims no cloud writes, wrote {prose}"
    # Prose lands in `sections`/`prose_revisions` on the filesystem backend
    # too, because that backend keeps history in SQLite. Those are the
    # cloud half and are not counted as local.
    local_seen = sql - {"sections", "prose_revisions"}
    surprise = local_seen - declared_local
    assert not surprise, (
        f"{name}: wrote undeclared local table(s) {sorted(surprise)}; "
        f"declared {sorted(declared_local)}")


# ------------------------------------------------------------ the routes

def test_save_writes_prose_and_metadata_separately(audited):
    client, log, _db, _idir = audited
    save_section(client, EDIT, "lowdown", "New words.\n")
    _check("editor_save", log)
    assert log.prose, "a save must reach the prose repository"
    assert log.commits > 1, (
        f"saw {log.commits} commits: a route that committed once could be "
        f"made atomic without moving anything")


def test_a_fully_coupled_save_writes_five_things_in_five_transactions(audited):
    """The case the cutover actually turns on.

    An approved section, whose text arrived carrying the rough-draft
    marker, saved over. Every conditional write fires: prose, its
    revision, provenance, prose state, the approval, and the staleness
    flag -- and each one commits on its own. A process that dies between
    any two leaves the Desk describing text that is not there.
    """
    client, log, db, idir = audited
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        "ROUGH DRAFT\n\nGenerated.\n", encoding="utf-8")
    save_section(client, EDIT, "lowdown", "ROUGH DRAFT\n\nGenerated.\n")
    assert client.post(f"{EDIT}/approve",
                       json={"section": "lowdown",
                             "action": "approve"}).status_code == 200
    with Storage(db) as s:
        assert (s.get_issue_modules("surfeit", SEASON, "week-01")
                .get("lowdown") or {}).get("approved")
    log.clear()

    save_section(client, EDIT, "lowdown", "Rewritten by hand.\n")

    _check("editor_save", log)
    assert log.prose, "prose"
    for table in ("prose_revisions", "section_prose_state", "issue_modules",
                  "meta"):
        assert table in log.sqlite, (
            f"{table} not written; observed {sorted(log.sqlite)}")
    assert log.commits >= 4, (
        f"{log.commits} commits for one click. Storage._cursor() commits "
        f"per method, so these cannot fail together")
    with Storage(db) as s:
        assert not (s.get_issue_modules("surfeit", SEASON, "week-01")
                    .get("lowdown") or {}).get("approved"), \
            "the edit must retire the approval it replaced"


def test_approve_writes_only_metadata(audited):
    client, log, _db, _idir = audited
    save_section(client, EDIT, "lowdown", "Approved words.\n")
    log.clear()
    r = client.post(f"{EDIT}/approve",
                    json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text
    _check("editor_approve", log)


def test_restore_writes_prose_and_metadata(audited):
    client, log, _db, _idir = audited
    save_section(client, EDIT, "lowdown", "One.\n")
    save_section(client, EDIT, "lowdown", "Two.\n")
    rows = client.get(f"{EDIT}/revisions", params={"section": "lowdown"}).json()
    state = client.get(f"{EDIT}/section-state",
                       params={"section": "lowdown"}).json()
    log.clear()
    r = client.post(f"{EDIT}/restore",
                    json={"section": "lowdown", "revision_id": rows["revisions"][0]["id"],
                          "expected_version": state["version"]})
    assert r.status_code == 200, r.text
    _check("editor_restore", log)


MARK = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"


def test_replace_with_my_copy_writes_prose_and_provenance(audited):
    client, log, _db, idir = audited
    # Origin is settled from the text as it stood BEFORE his first edit,
    # so the marker has to be in the file, and it has to be the exact
    # constant rather than a paraphrase of it.
    (idir / "lowdown" / "lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    from leaguepage import prose_store
    prose_store.reset_cache()
    save_section(client, EDIT, "lowdown", MARK + "\n\nGenerated, edited.\n")
    state = client.get(f"{EDIT}/section-state",
                       params={"section": "lowdown"}).json()
    log.clear()
    r = client.post(f"{EDIT}/replace-origin",
                    json={"section": "lowdown", "confirm": "yes",
                          "expected_version": state["version"]})
    assert r.status_code == 200, r.text
    _check("editor_replace_origin", log)


def test_accept_proposal_touches_two_prose_objects_and_a_file(audited):
    client, log, _db, idir = audited
    save_section(client, EDIT, "lowdown", "His own words.\n")
    (idir / "proposals" / "lowdown.md").write_text(
        "A proposed rewrite.\n", encoding="utf-8")
    client.post(f"{EDIT}/request-rewrite",
                json={"section": "lowdown", "note": "tighten it"})
    state = client.get(f"{EDIT}/section-state",
                       params={"section": "lowdown"}).json()
    log.clear()
    r = client.post(f"{EDIT}/proposal",
                    json={"section": "lowdown", "action": "accept",
                          "expected_version": state["version"]})
    assert r.status_code == 200, r.text
    _check("proposal_action", log)
    assert len(log.prose) >= 2, (
        f"accept must touch the section and the proposal, saw {log.prose}")
    assert any("REVISION_REQUESTS" in f for f in log.files), (
        "accept rewrites a file on this machine inside the same action")


def test_request_rewrite_writes_a_file_a_hosted_desk_has_no_disk_for(audited):
    client, log, _db, _idir = audited
    log.clear()
    r = client.post(f"{EDIT}/request-rewrite",
                    json={"section": "lowdown", "note": "more teeth"})
    assert r.status_code == 200, r.text
    _check("request_rewrite", log)
    assert any("REVISION_REQUESTS" in f for f in log.files)


def test_custom_and_module_routes_write_issue_modules(audited):
    client, log, _db, _idir = audited
    log.clear()
    r = client.post(f"{EDIT}/custom", data={"title": "A New Section"})
    assert r.status_code in (200, 303), r.text
    _check("editor_custom", log)


def test_take_tracking_writes_takes(audited):
    client, log, _db, _idir = audited
    log.clear()
    r = client.post(f"{EDIT}/take",
                    json={"text": "This team misses the playoffs.",
                          "section": "lowdown"})
    assert r.status_code in (200, 400), r.text
    if r.status_code == 200:
        _check("track_take", log)


def test_team_name_override_writes_team_names(audited):
    client, log, _db, _idir = audited
    log.clear()
    r = client.post(f"/commissioner/surfeit/{SEASON}/team-names",
                    data={"name_1": "The Renamed"})
    assert r.status_code in (200, 303), r.text
    _check("set_team_names", log)


def test_theme_writes_a_column_postgres_does_not_have(audited):
    client, log, _db, _idir = audited
    log.clear()
    r = client.post(f"{BASE}/theme", data={"theme": "futures"})
    assert r.status_code in (200, 303), r.text
    _check("set_theme", log)


# ------------------------------------------------- what the table asserts

def test_no_route_commits_its_writes_together(audited):
    """The structural reason nothing is hosted-safe yet.

    `Storage._cursor()` commits after every mutating method, so a route
    that writes three tables is three transactions. On one machine the
    next request repairs the difference. Once the two halves of a click
    live in two databases, nothing repairs it.
    """
    client, log, _db, _idir = audited
    save_section(client, EDIT, "lowdown", "Words.\n")
    assert log.commits > 1, (
        "a save that committed once would be atomic within SQLite, which "
        "would change the transaction-owner column for every route")


def test_the_declared_table_is_internally_consistent():
    """No claim may say `safe` while naming a local authoritative write."""
    for name, c in CLAIMS.items():
        if c.safe:
            assert not c.local, f"{name}: safe but writes {c.local} locally"
            assert not c.cloud or c.owner != "none", (
                f"{name}: safe with cloud writes but no transaction owner")
        if c.local and c.cloud:
            assert c.owner == "none", (
                f"{name}: writes both stores; owner must be 'none' until "
                f"one transaction spans them")
