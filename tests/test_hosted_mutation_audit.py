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
    """What a route writes, who commits it, and what survives a restart.

    `restart_safe` and `safe` are deliberately separate columns. Locally
    crash-consistent means: kill the process anywhere inside this route
    and the application never afterwards presents metadata that falsely
    describes the prose that survived. Hosted-safe additionally means the
    route performs no authoritative write to this machine. Almost every
    authoring route is now the first and none is the second.
    """

    cloud: tuple[str, ...]          # would land in Postgres after a cutover
    local: tuple[str, ...]          # stays on this machine and is authoritative
    # Who commits the SQLite writes together. "route" is a route holding
    # its own transaction; "store" is a route that has given that up and
    # expresses intent to EditorialStore instead, which is the shape a
    # cutover needs -- on Postgres the store's action is one transaction
    # covering the prose as well. "store" is a claim about SHAPE and is
    # cross-checked structurally in tests/test_prose_routes_are_store_owned.py,
    # because a route can call the store and still write beside it.
    owner: str
    safe: bool                      # hosted-safe TODAY
    why: str
    signature: str = ""             # what a content signature protects here
    fs_dep: str = ""                # remaining local filesystem dependency
    restart_safe: bool = True       # cannot lie after a crash + restart
    exercised: bool = True
    kind: str = "authoring"         # authoring | operational | auth | publish
    # Would a hosted Desk put this control in front of him? Authoring is
    # the product; publication and the research build are deliberately
    # local for the first hosted beta. A route that is exposed and unsafe
    # is a blocker; one that is not exposed is a scope decision.
    hosted_exposed: bool = True


def C(cloud=(), local=(), owner="none", safe=False, why="", **kw) -> Claim:
    return Claim(tuple(cloud), tuple(local), owner, safe, why, **kw)


# Prose lives in the repository, so it is the one thing that already moves.
# Everything else in `local` is a SQLite table with no cutover path yet, or
# a file under editorial/.
P = ("sections", "prose_revisions")

CLAIMS: dict[str, Claim] = {
    # ---------------------------------------------------------- authoring
    "editor_save": C(
        P, ("prose_provenance", "section_prose_state", "matchup_state"),
        owner="store",
        signature="approval retires itself; provenance claims nothing",
        why="the route decides and the store writes. On the filesystem "
            "that is still two commits -- prose, then everything "
            "describing it -- so a crash between them leaves prose with "
            "no description, which is honest rather than wrong. On "
            "Postgres the same intent is one transaction"),
    "editor_restore": C(
        P, ("section_prose_state", "matchup_state"), owner="store",
        signature="approval retires itself, and returns if the exact text does",
        why="same seam as save, reached from the History panel. The "
            "revision is now read inside the action, so it cannot be "
            "deleted between being chosen and being used"),
    "editor_reset_generated": C(
        P, ("prose_provenance", "section_prose_state", "matchup_state"),
        owner="store", signature="approval and provenance both content-bound",
        why="the rough draft it resets to is read through the research "
            "port now -- a file here, a `research_artifacts` row in the "
            "cloud. It used to stat the issue directory, which on a "
            "hosted Desk answers 'no draft' forever and silently"),
    "editor_replace_origin": C(
        P, ("prose_provenance", "section_prose_state", "matchup_state"),
        owner="store",
        signature="provenance is hashed over the text, so a lost write "
                  "claims nothing rather than the wrong author",
        why="clears the section and rewrites authorship in one action. "
            "The origin it refuses on is now READ inside that action, "
            "which closes a check-then-act race the old route had"),
    "proposal_action": C(
        P, ("prose_provenance", "section_prose_state", "matchup_state",
            "issue_revision_requests"),
        owner="store", restart_safe=False,
        signature="approval and provenance both content-bound",
        fs_dep="deletes the proposal file; rewrites REVISION_REQUESTS.md",
        why="the metadata is one transaction, but accepting is TWO prose "
            "objects and the filesystem cannot join them: a crash between "
            "them re-offers a proposal already accepted. Truthful and "
            "recoverable, not atomic"),
    "editor_approve": C(
        (), ("issue_modules", "matchup_state"), owner="store",
        signature="records the signature of exactly what it approves",
        why="approval is a claim about a particular text, and CTP "
            "additionally records what each preview said. The signature "
            "is now READ through the same action that writes it, so an "
            "approval cannot describe a version a save replaced in "
            "between; CTP's up-to-seven writes are one intent"),
    "matchup_draft_save": C(
        P, ("matchup_state",), owner="store",
        signature="CTP's approval covers this text and retires itself",
        why="the matchup half of save, reached from the week page. The "
            "draft and the stage it moves to now travel together"),
    "lowdown_save": C(
        P, ("section_prose_state", "issue_modules"), owner="store",
        signature="the Lowdown's approval is a signature over its text",
        why="the Lowdown screen's own save and its own Approve control, "
            "both through the store now that the signature can be read "
            "on the action that records it"),
    "request_rewrite": C(
        (), ("issue_revision_requests",), owner="store",
        fs_dep="regenerates REVISION_REQUESTS.md (derived, not a source)",
        why="the queue is SQLite and authoritative; the file is derived "
            "from it and repaired on the next Issue Room load"),
    "editor_custom": C((), ("issue_modules",), owner="store",
                       why="adds a custom section row. Choosing the free "
                           "key and writing it are one action, so two "
                           "clicks cannot pick the same number"),
    "editor_module": C((), ("issue_modules",), owner="store",
                       why="include/exclude/reorder"),
    "issue_module_update": C((), ("issue_modules",), owner="store",
                             why="the builder's copy, signature and all"),
    "editor_rankings": C((), ("power_rankings", "prose_provenance"),
                         owner="store",
                         signature="the notes ARE Peer and Near-Peer's prose",
                         why="the table and the claim that the notes are "
                             "his were two transactions; a failure between "
                             "them saved the ranking and lost the claim"),
    "rankings_save": C((), ("power_rankings", "prose_provenance"),
                       owner="store", why="the standalone page, same intent"),
    "set_theme": C((), ("issues",), owner="store",
                   why="the issue's theme. `issues.theme` exists in "
                       "Postgres since 0006"),
    "set_team_names": C((), ("team_names",), owner="store",
                        why="one click renames as many teams as the form "
                            "carries, in one transaction"),
    "use_sleeper_name": C((), ("team_names",), owner="store",
                          why="clears one override"),
    "matchup_angle": C((), ("matchup_state", "story_decisions"),
                       owner="store",
                       why="selecting an angle IS deciding the candidate is "
                           "in. Two writes, one intent: a preview at "
                           "ready-to-draft with no decision behind it is "
                           "how a story gets written twice"),
    "matchup_prominence": C((), ("matchup_state",), owner="store",
                            why="prominence override"),
    "matchup_revision": C((), ("matchup_state",), owner="store",
                          why="read-modify-write on a list, now inside one "
                              "transaction, so two requests filed at once "
                              "cannot lose one. The column exists in "
                              "Postgres since 0006"),
    "matchup_status_change": C((), ("matchup_state", "editorial_usage"),
                               owner="store",
                               why="workflow stage, not a publication claim "
                                   "-- but approving also writes the "
                                   "repetition log, and a log entry for an "
                                   "approval that did not happen would "
                                   "suppress a joke he never told"),
    "story_decide": C((), ("story_decisions",), owner="store",
                      why="story routing"),
    "decide_story": C((), ("story_decisions",), owner="store",
                      why="draft-review copy"),
    "award_decide": C((), ("award_decisions",), owner="store",
                      why="award decisions"),
    "decide_award": C((), ("award_decisions",), owner="store",
                      why="draft-review copy"),
    "save_power": C((), ("power_rankings", "prose_provenance"),
                    owner="store", why="draft-review copy, same intent"),
    "track_take": C((), ("takes",), owner="store", why="Track This Take"),
    "add_take": C((), ("takes",), owner="store", why="draft-review copy"),
    "take_action": C((), ("takes",), owner="store",
                     why="status, public flag, delete. The take is read "
                         "inside the action, so 'is this take in this "
                         "league' cannot go stale before the write"),
    "resolve_take": C((), ("takes",), owner="store", why="resolution"),
    "false_assumption_decide": C((), ("takes", "story_decisions"),
                                 owner="store",
                                 why="two tables across its branches, but "
                                     "one click takes exactly one branch. "
                                     "Its verdicts now name the statuses "
                                     "0003 left in the data instead of the "
                                     "pre-lifecycle words Storage was "
                                     "quietly translating"),
    "force_flow_note": C((), ("force_flow_notes",), owner="store",
                         why="a note against one transaction. The table "
                             "exists in Postgres since 0006 and has no "
                             "caller there yet"),
    "inbox_decide": C((), ("story_decisions",), owner="store",
                      why="Change Inbox ruling"),
    "inbox_reviewed": C((), ("sync_snapshots",), owner="store",
                        why="marks a baseline. `sync_snapshots` carries "
                            "two different things: the payload rows are "
                            "CACHE that a resync rebuilds, and "
                            "`reviewed_at` is an EDITORIAL claim that he "
                            "has seen those changes. The import carries "
                            "neither, because a hosted Desk syncs for "
                            "itself and a `reviewed_at` copied onto a "
                            "snapshot that no longer exists would mark "
                            "unseen changes as seen"),
    "qa_action": C(P, ("section_prose_state", "matchup_state"),
                   owner="store",
                   signature="an accepted fix is a prose write like any other",
                   why="dismisses a warning, or applies its mechanical fix",
                   exercised=False),
    # ------------------------------------------------------- operational
    "review_packet_save": C((), (), owner="n/a", safe=False, kind="operational",
                            fs_dep="RECOMPUTABLE RESEARCH: REVIEW_PACKET.md",
                            why="the packet is a rendering of state that lives "
                                "elsewhere, and it is written for a Claude Code "
                                "session to read. It exists as a route at all "
                                "because the GET that used to write it made "
                                "reading the review screen a repository change; "
                                "the writing is now an act with a button behind "
                                "it, and the same recomputable-research "
                                "resolution as issue_build applies",
                            exercised=False),
    "issue_build": C((), (), owner="n/a", safe=False, kind="operational",
                     fs_dep="RECOMPUTABLE RESEARCH: briefs, packets, "
                            "generated JSON",
                     why="RESOLVED as operational and out of hosted scope. "
                         "It writes nothing authoritative -- every byte is "
                         "recomputable from synced data -- and it is a step "
                         "in a Claude Code authoring session, which already "
                         "requires a machine with the repository on it. The "
                         "one artifact the APPLICATION reads back is the "
                         "Lowdown rough draft, and that now goes through "
                         "the research port", exercised=False),
    "sync_start": C((), (), owner="job", safe=False, kind="operational",
                    why="starts a durable job; the job writes Sleeper cache "
                        "and snapshot rows, both of which a hosted Desk "
                        "would rebuild for itself from Sleeper. Nothing it "
                        "writes is authored, which is why the import "
                        "carries none of it", exercised=False),
    # `local` is empty on purpose, and it is the only authoring route where
    # that is true. About has ONE destination and the backend chooses which:
    # `site_documents` on Postgres, `editorial/site/about.md` on the
    # filesystem. That is the same convention prose already uses -- prose
    # lands in `sections`/`prose_revisions` on both backends and is counted
    # as cloud -- so the file belongs in `fs_dep`, not in `local`.
    "about_save": C(("site_documents",), (),
                    owner="store", kind="authoring",
                    fs_dep="ON THE FILESYSTEM BACKEND ONLY: "
                           "editorial/site/about.md. On Postgres the row is "
                           "authoritative and no file is written at all",
                    why="CLOSED. The ProseKey problem was real -- a key is "
                        "(league, season, issue, kind, name) and the About "
                        "page has none of those -- so it got a table of its "
                        "own instead of an invented key: site_documents, "
                        "migration 0007. The route now expresses intent to "
                        "the store like every other authoring route, so on "
                        "Postgres the save is one transaction as the "
                        "signed-in Commissioner with RLS applying to it. "
                        "Still `safe=False` for the same reason as every "
                        "other store-owned route: the selected backend "
                        "today is the filesystem, and that is where the "
                        "write lands"),
    "about_preview": C((), (), owner="n/a", safe=True, kind="operational",
                       why="renders to a temp path, writes nothing "
                           "authoritative", exercised=False),
    # ----------------------------------------------------------- publish
    "issue_publish": C((), ("published/**.json", "issues"), kind="publish",
                       fs_dep="PUBLICATION ARTIFACT: the immutable snapshot",
                       why="out of scope for hosted execution by decision, "
                           "not by defect", exercised=False),
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
    assert len(unsafe) == 36, sorted(unsafe)


def test_the_store_owned_routes_are_exactly_the_ones_proved_to_be():
    """`owner="store"` is not a label anyone may hand out.

    The structural half of the claim lives in
    tests/test_prose_routes_are_store_owned.py, which reads each route's
    source and refuses it if it still writes anything authoritative
    itself. Pinning the two lists against each other means a route cannot
    be marked moved in one file without being proved moved in the other.
    """
    from test_prose_routes_are_store_owned import MOVED, PARTIAL

    claimed = {n for n, c in CLAIMS.items() if c.owner == "store"}
    assert claimed == set(MOVED), sorted(claimed ^ set(MOVED))
    for name in PARTIAL:
        assert CLAIMS[name].owner != "store", (
            f"{name} is half migrated and must not read as moved")


def test_moving_a_route_did_not_move_the_cutover():
    """Eight routes changed owner. None of them changed the answer.

    Hosted safety is about WHERE the authoritative write lands, and it
    still lands on this machine for every one of them. A route with a
    better transaction owner and the same destination is a better route
    and not a hosted one.
    """
    for name, claim in CLAIMS.items():
        if claim.owner != "store":
            continue
        assert not claim.safe, f"{name} claims hosted safety"
        if not claim.local:
            # The conversation this used to demand. `about_save` is the
            # first authoring route with NO local remainder: its whole
            # authoritative state has a cloud home and no SQLite caller
            # left behind. It is still not hosted-safe, because the
            # selected backend today is the filesystem and that is where
            # the write lands -- but the reason is now the setting rather
            # than the code.
            assert name in {"about_save"}, (
                f"{name} claims to write nothing local; if that is true it "
                "is a cutover candidate and belongs in that conversation")
            assert claim.fs_dep, (
                f"{name} writes nothing local and names no filesystem "
                "dependency; one of those is wrong")


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


def test_a_fully_coupled_save_has_two_transaction_owners_not_five(audited):
    """The case the cutover turns on, after Tranche 5A.

    An approved section whose text arrived carrying the rough-draft
    marker, saved over. Every conditional write still fires -- prose, its
    revision, provenance, prose state -- but the counting is different:

      one commit  the repository writing the prose and its revision
      one commit  the route writing everything that DESCRIBES that prose

    Two owners, not five transactions, and the second is exactly the one
    the Postgres backend will absorb into the first. Nothing writes to
    the approval at all: it carries a signature, so the save retired it
    by moving the text.
    """
    client, log, db, idir = audited
    # The marker settles an AI origin from the text as it stood BEFORE his
    # first edit. It must not survive into what he approves: approval over
    # a marked draft is refused, which is the point of the marker.
    (idir / "lowdown" / "lowdown.md").write_text(MARK + "\n\nGenerated.\n",
                                                 encoding="utf-8")
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        MARK + "\n\nGenerated.\n", encoding="utf-8")
    from leaguepage import prose_store
    prose_store.reset_cache()
    save_section(client, EDIT, "lowdown", "Edited into shape.\n")
    r = client.post(f"{EDIT}/approve",
                    json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text
    assert _effective(db), "approved to begin with"
    log.clear()

    save_section(client, EDIT, "lowdown", "Rewritten by hand.\n")

    _check("editor_save", log)
    assert log.prose, "prose"
    for table in ("prose_revisions", "section_prose_state", "prose_provenance"):
        assert table in log.sqlite, (
            f"{table} not written; observed {sorted(log.sqlite)}")
    assert "issue_modules" not in log.sqlite, (
        "nothing writes to the approval; the signature already retired it")
    assert "meta" not in log.sqlite, (
        "staleness is a comparison now, not a row somebody has to set")
    assert log.commits == 2, (
        f"{log.commits} commits. Expected two owners: the repository for "
        f"prose + its revision, the route for everything describing it")
    assert not _effective(db), "the edit retired the approval it replaced"


def _effective(db, module="lowdown"):
    from leaguepage.issue_builder import module_approved, module_states

    with Storage(db) as s:
        kinds = {m["module_key"]: m["kind"]
                 for m in module_states(s, LG, SEASON, "week-01", week=1)}
        return module_approved(s, LG, SEASON, "week-01", module,
                               kinds.get(module, "section"), 1)[0]


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
    """The rules the table has to obey, so a claim cannot be self-serving.

    A route that writes both stores may now name a SQLite transaction
    owner -- that is what Tranche 5A built -- but it may never call
    itself hosted-safe on that basis, because no SQLite transaction spans
    a filesystem write. It may only call itself restart-safe if a content
    signature covers the gap, and it has to say which one.
    """
    for name, c in CLAIMS.items():
        if c.safe:
            assert not c.local, f"{name}: safe but writes {c.local} locally"
            assert not c.fs_dep, f"{name}: safe but depends on {c.fs_dep}"
        if c.local and c.cloud:
            assert not c.safe, (
                f"{name}: writes both stores, so it cannot be hosted-safe "
                f"however well the local half commits")
            if c.restart_safe:
                assert c.signature, (
                    f"{name}: a route spanning two stores can only be "
                    f"restart-safe because a content signature covers the "
                    f"gap, and it has to name which one")
            else:
                assert c.why, f"{name}: says it can lie, without saying how"


def test_locally_crash_consistent_is_not_the_same_column_as_hosted_safe():
    """The distinction this tranche exists to make.

    After 5A most authoring routes cannot lie after a crash. None of them
    became hosted-safe, because nothing about a local transaction removes
    a local filesystem write. When the second number moves, it will be
    because Tranche 5B replaced the local transaction owner with the
    Postgres one -- not because this file was edited.
    """
    authoring = [c for c in CLAIMS.values() if c.kind == "authoring"]
    consistent = [c for c in authoring if c.restart_safe]
    hosted = [c for c in authoring if c.safe]
    assert len(consistent) == len(authoring) - 1, (
        "every authoring route but proposal accept is crash-consistent")
    assert hosted == [], "no authoring route is hosted-safe yet"


def test_the_cutover_gate_names_its_blockers(store_free=None):
    """The gate, stated as the number it actually turns on.

    Cutover requires every HOSTED-EXPOSED authoring route to be safe.
    Tranche 5B built the transaction that makes that possible and wired
    none of the routes to it, so the number has not moved -- and this
    test exists so that claim is measured rather than asserted in a
    document.
    """
    exposed = {n: c for n, c in CLAIMS.items()
               if c.kind == "authoring" and c.hosted_exposed}
    blockers = sorted(n for n, c in exposed.items() if not c.safe)
    assert len(exposed) == 36, len(exposed)
    assert len(blockers) == 36, (
        f"{len(exposed) - len(blockers)} route(s) now claim hosted safety; "
        f"re-read them and move the gate deliberately")


def test_the_prose_routes_are_the_ones_a_cloud_transaction_can_own():
    """Which routes EditorialStore could take over, and which need their
    own cloud state first. Nine reach the cloud -- eight prose routes and
    About, which got `site_documents` in migration 0007 -- and twenty-seven
    write only SQLite tables that have a Postgres home but no caller."""
    authoring = {n: c for n, c in CLAIMS.items() if c.kind == "authoring"}
    prose = {n for n, c in authoring.items() if c.cloud}
    sqlite_only = {n for n, c in authoring.items() if not c.cloud}
    assert len(prose) == 9, sorted(prose)
    assert len(sqlite_only) == 27, len(sqlite_only)
    assert prose | sqlite_only == set(authoring)
