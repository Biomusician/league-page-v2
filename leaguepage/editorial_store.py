"""One Commissioner action, one transaction owner.

    with editorial_store().action(actor=email) as act:
        saved = act.save_section(key, text, expected_version=v)

The route says what it wants. The backend decides what a transaction is.

WHY THE ROUTE MUST NOT DO THIS ITSELF

Tranche 5A's route code opened a prose write and then a SQLite
transaction, which is two owners, and the fault injection proved exactly
what that costs: the prose lands, the description does not, and only the
content-bound claims stop the Desk from lying about it. That is the best
a filesystem can do. Postgres can do better, but only if something owns
the whole action -- a route that calls two repositories and commits each
of them has written a distributed transaction by accident.

WHAT EACH BACKEND PROMISES

  filesystem  the prose write is its own commit; everything describing it
              is one SQLite transaction. Two owners, unchanged from 5A,
              and the content-bound claims carry the gap.

  postgres    prose, its revision, its state, its provenance and the
              metadata around it are ONE transaction. Either the whole
              click happened or none of it did.

The route cannot tell which, which is the point: the semantics are the
same and only the strength of the guarantee differs.

AUTHORIZATION

The Postgres backend asserts the signed-in Commissioner's identity into
every transaction, so RLS evaluates the same `commissioner_all` policy a
browser would get. See docs/DECISIONS.md, 2026-09-08. `actor` is that
identity and is required: a nameless action is one the database cannot
authorize.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from leaguepage import prose_store
from leaguepage.editorial_state import (PostgresEditorialState,
                                        SqliteEditorialState)
from leaguepage.prose_store import (Prose, ProseError, ProseKey,
                                    UnknownRevision)

# What a save records about who wrote the text, when the Desk can tell.
COMMISSIONER = "commissioner"

# "the caller did not say" is distinguishable from "the caller said None".
_UNSET = object()


class EditorialAction:
    """The operations of one Commissioner click, bound to one transaction.

    Holds a prose repository and an editorial-state port that are both
    already inside whatever transaction the store opened. Nothing here
    commits: the store does that when the block ends.
    """

    def __init__(self, prose, state, *, actor: str, atomic: bool) -> None:
        self.prose = prose
        self.state = state
        self.actor = actor
        # True when prose and metadata share one transaction. Routes do not
        # branch on this; tests and /health report it, because "the click
        # is atomic" is a promise worth being able to check.
        self.atomic = atomic

    # -- the shared tail of every prose-writing action ------------------

    def describe(self, key: ProseKey, *, state: str | None = None,
                 provenance_row: dict | None = None,
                 assistance: str | None = None,
                 stage_back_from: int | None = None) -> None:
        """Record what is true about the prose that was just written.

        Deliberately one call. These four writes always travel together
        and the ordering between them has never mattered; what matters is
        that they are inside the same transaction as the write they
        describe, which is the caller's business and not theirs.
        """
        if provenance_row is not None:
            self.state.set_provenance(key, provenance_row)
        if assistance is not None:
            self.state.set_assistance(key, assistance)
        if state is not None:
            self.state.set_prose_state(key, state)
        if stage_back_from is not None and key.kind == prose_store.MATCHUP:
            row = self.state.matchup(key.league, key.season, stage_back_from,
                                     key.name) or {}
            if (row.get("status") or "") in ("approved", "locked"):
                # A preview he has just rewritten is not at the approved
                # stage. Bookkeeping, not a claim: what CTP publishes is
                # governed by its signature, which retired itself when the
                # text moved.
                self.state.set_matchup(key.league, key.season,
                                       stage_back_from, key.name,
                                       status="edited")

    # -- intent --------------------------------------------------------

    def save_section(self, key: ProseKey, text: str, *,
                     expected_version: str | None,
                     source: str = "commissioner-save",
                     week: int | None = None,
                     state: str | None = "commissioner-edited",
                     prior_version=_UNSET,
                     describe_unchanged: bool = False,
                     provenance_row: dict | None = None,
                     assistance: str | None = None) -> Prose:
        """Write prose and everything that describes it.

        Raises ProseConflict before anything is written when the caller's
        version is stale, so a refused save leaves no revision, no state
        change, no provenance and no approval change -- on either backend.

        `prior_version` is what was STORED before this call, which is the
        only thing that can answer "were these the same bytes twice". The
        caller's expected version is a weaker stand-in and is wrong
        outright for a client that sends no version, so a route that has
        already read the record should say what it read.

        `describe_unchanged` is for acts that are about origin rather than
        about words. Accepting a proposal that happens to match the text
        already there is still Claude's wording being adopted, and the
        Desk should say so.
        """
        saved = self.prose.put(key, text, expected_version=expected_version,
                               source=source)
        stored = expected_version if prior_version is _UNSET else prior_version
        if saved.version == stored and not describe_unchanged:
            # The same bytes saved twice. Not an edit, so nothing that
            # describes the text needs to change either.
            return saved
        self.describe(key, state=state, provenance_row=provenance_row,
                      assistance=assistance, stage_back_from=week)
        return saved

    def restore(self, key: ProseKey, revision_id: int, *,
                expected_version: str | None,
                week: int | None = None) -> Prose:
        """Put back a text from history. The revision is read inside the
        transaction, so it cannot be deleted between choosing and using."""
        rev = self.prose.revision(revision_id)
        if not rev or (rev["league_slug"], rev["season"], rev["issue_key"],
                       rev["section"]) != (key.league, key.season, key.issue,
                                           key.section_id):
            raise UnknownRevision(str(revision_id))
        return self.save_section(key, rev["prior_text"],
                                 expected_version=expected_version,
                                 source="restore", week=week)

    def accept_proposal(self, target: ProseKey, proposal: ProseKey, text: str, *,
                        expected_version: str | None,
                        proposal_version: str | None = None,
                        week: int | None = None,
                        provenance_row: dict | None = None) -> Prose:
        """The route Tranche 5A could not make atomic.

        Two prose objects: write the target, retire the proposal. On the
        filesystem those are two files and a crash between them re-offers
        an accepted rewrite; on Postgres they are two rows in one
        transaction and that state cannot exist.

        Retirement is idempotent either way: deleting a proposal that is
        already gone is success, because the outcome the caller asked for
        is the outcome.
        """
        if proposal_version is not None:
            current = self.prose.get(proposal)
            if current.version != proposal_version:
                raise prose_store.ProseConflict(
                    proposal, proposal_version, current.version, current.text)
        saved = self.save_section(target, text,
                                  expected_version=expected_version,
                                  source="proposal-accept", week=week,
                                  describe_unchanged=True,
                                  provenance_row=provenance_row)
        self.prose.delete(proposal)
        self.state.resolve_rewrite_requests(target.league, target.season,
                                            target.issue, target.section_id,
                                            "done")
        return saved

    def approve(self, league: str, season: str, issue: str, module_key: str,
                signature: str, *, covered: dict[str, str] | None = None) -> None:
        """Record the approval AND what it covers, together.

        `signature` is observed by the caller from the same state this
        transaction sees. `covered` is CTP's per-preview coverage: not an
        approval of its own, just what each preview said at the moment the
        one approval was given.
        """
        self.state.set_module(league, season, issue, module_key,
                              approved=1, approved_sha=signature)
        for (week, slug), sha in (covered or {}).items():
            self.state.set_matchup(league, season, week, slug, covered_sha=sha)

    def unapprove(self, league: str, season: str, issue: str, module_key: str,
                  *, covered_weeks: list[tuple[int, str]] | None = None) -> None:
        self.state.set_module(league, season, issue, module_key,
                              approved=0, approved_sha=None)
        for week, slug in (covered_weeks or []):
            self.state.set_matchup(league, season, week, slug, covered_sha=None)


class FilesystemEditorialStore:
    """Prose on disk, metadata in SQLite. Two owners, as in Tranche 5A."""

    backend = prose_store.FILESYSTEM
    atomic = False

    def __init__(self, db_path=None, *, base_dir=None) -> None:
        self._db_path = db_path
        self._base_dir = base_dir

    @contextmanager
    def action(self, *, actor: str = "") -> Iterator[EditorialAction]:
        from leaguepage.config import DB_PATH
        from leaguepage.storage import Storage

        repo = prose_store.FilesystemProseRepository(
            db_path=self._db_path, base_dir=self._base_dir)
        with Storage(self._db_path or DB_PATH) as s, s.transaction():
            yield EditorialAction(repo, SqliteEditorialState(s),
                                  actor=actor, atomic=False)

    def health(self) -> dict:
        return {"backend": self.backend, "atomic_actions": False}


class PostgresEditorialStore:
    """Everything in one Postgres transaction, as the signed-in user.

    The connection is opened per action rather than pooled in-process:
    the hosting target is serverless, where a process may handle one
    request and disappear, and a connection held across requests is a
    connection leaked.
    """

    backend = prose_store.POSTGRES
    atomic = True

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def dsn(self) -> str:
        if self._dsn:
            return self._dsn
        from leaguepage import settings

        value = settings.get(settings.DATABASE_URL)
        if not value:
            raise ProseError(
                "the editorial store is set to postgres but DATABASE_URL is "
                "not configured. Refusing to fall back to the filesystem: "
                "two half-populated sources of truth is worse than not "
                "starting.")
        return value

    @contextmanager
    def action(self, *, actor: str = "") -> Iterator[EditorialAction]:
        try:
            import psycopg
        except ImportError as exc:                              # pragma: no cover
            raise ProseError("the postgres editorial store needs psycopg "
                             "installed") from exc
        if not actor:
            # Not a formality. The database authorizes on this, so an
            # action without it would either be refused by RLS or -- worse
            # -- run as the owner and skip the policy entirely.
            raise ProseError("an editorial action needs the signed-in "
                             "Commissioner's identity")
        from leaguepage.prose_postgres import PostgresProseRepository

        with psycopg.connect(self.dsn(), connect_timeout=20) as conn:
            with conn.cursor() as cur:
                # Drop out of the owner role for the whole action, and say
                # who is acting. RLS applies on the CURRENT role, so this
                # makes `commissioner_all` evaluate exactly as it would for
                # a browser -- on the half of the application that writes.
                cur.execute("select set_config('request.jwt.claims', %s, true)",
                            (json.dumps({"role": "authenticated",
                                         "email": actor}),))
                cur.execute("set local role authenticated")
                repo = PostgresProseRepository(self._dsn).bound_to(cur)
                yield EditorialAction(repo, PostgresEditorialState(cur),
                                      actor=actor, atomic=True)
            # psycopg commits here, or rolls back if the block raised.

    def health(self) -> dict:
        return {"backend": self.backend, "atomic_actions": True}


def store(db_path=None, *, base_dir=None, backend: str | None = None):
    """The one editorial store for this run.

    Selected by the same setting the prose repository uses, because a run
    with prose in one store and its metadata in another is the split brain
    every tranche so far has been avoiding.
    """
    chosen = backend or prose_store.backend_name()
    if chosen == prose_store.POSTGRES:
        return PostgresEditorialStore()
    if chosen == prose_store.FILESYSTEM:
        return FilesystemEditorialStore(db_path, base_dir=base_dir)
    raise ProseError(f"unknown editorial backend: {chosen!r}")
