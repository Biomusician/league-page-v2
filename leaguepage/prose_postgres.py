"""The Postgres implementation of the same prose contract.

Nothing above `ProseRepository` can tell this apart from the filesystem
backend, which is the whole point: moving the source of truth is meant to
be a configuration change, not a rewrite. What differs is underneath.

* **The version is a counter, not a hash.** Nothing edits this table
  except the application, so there is no back door for an edit the store
  did not see, and counting is cheaper than hashing. (The filesystem
  backend cannot say that: `editorial/` is a working tree that Jonathan
  edits directly, so it derives its version from the bytes.)

* **A write is one guarded statement.** `update ... where version = %s`,
  and a rowcount of zero IS the conflict. Nothing reads a version and
  updates on a later line, because two hosted instances would interleave
  exactly there.

* **Prose and its revision move together.** They are one transaction, so
  an edit that promises history cannot half-happen. The filesystem backend
  writes the revision first and hopes; here it is atomic.

This module never falls back. If Postgres is selected and unreachable, it
raises: a silent fallback to the filesystem would leave two half-populated
sources of truth, which is the failure the whole cutover design exists to
avoid.
"""
from __future__ import annotations

from contextlib import contextmanager

from leaguepage.prose_store import (KINDS, PROPOSAL, Prose, ProseConflict,
                                    ProseError, ProseKey, content_hash)

# The version token, so a caller cannot accidentally hand a filesystem
# token to Postgres or the reverse and have it silently mean something.
PREFIX = "pg1:"


def token(version: int) -> str:
    return f"{PREFIX}{version}"


def parse_token(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.startswith(PREFIX):
        raise ProseError(f"not a Postgres prose version: {value!r}")
    return int(value[len(PREFIX):])


class PostgresProseRepository:
    """Prose in the `sections` table, revisions in `prose_revisions`."""

    backend = "postgres"

    def __init__(self, db_path=None, *, dsn: str | None = None):
        # db_path is accepted and ignored so the factory can build either
        # backend from the same call. SQLite holds no prose here.
        self._dsn = dsn
        self._checked = False
        # When set, this repository is a passenger: somebody else opened
        # the transaction and will commit or roll it back. Used by
        # EditorialStore so one Commissioner action is one transaction.
        self._bound = None

    # -- connection ----------------------------------------------------

    def dsn(self) -> str:
        if self._dsn:
            return self._dsn
        from leaguepage import settings

        value = settings.get(settings.DATABASE_URL)
        if not value:
            raise ProseError(
                "the prose backend is set to postgres but DATABASE_URL is not "
                "configured. Refusing to fall back to the filesystem: two "
                "half-populated sources of truth is worse than not starting.")
        return value

    def bound_to(self, cursor) -> "PostgresProseRepository":
        """A view of this repository that runs on someone else's cursor.

        Returns a copy rather than mutating, so a bound repository cannot
        leak out of the transaction that made it.
        """
        clone = PostgresProseRepository(self._dsn)
        clone._bound = cursor
        return clone

    @contextmanager
    def _tx(self):
        if self._bound is not None:
            # A passenger. No connect, no commit, and crucially no
            # try/except that would turn somebody else's failure into a
            # ProseError and hide it from the transaction owner.
            yield self._bound
            return
        try:
            import psycopg
        except ImportError as exc:                              # pragma: no cover
            raise ProseError("the postgres prose backend needs psycopg "
                             "installed") from exc
        try:
            with psycopg.connect(self.dsn(), connect_timeout=20) as conn:
                with conn.cursor() as cur:
                    yield cur
        except (ProseError, ProseConflict):
            # ProseConflict is a SIBLING of ProseError, not a subclass, and
            # it is raised from inside this block by every guarded write.
            # Catching only ProseError reported every optimistic-concurrency
            # refusal as "the backend is unreachable": the Desk would have
            # shown a stale save as an outage and never reached the conflict
            # screen. Found by running the contract against a live database,
            # which is the only place it can be found.
            raise
        except Exception as exc:                                # noqa: BLE001
            # Never echo the DSN: it carries the password.
            raise ProseError(
                f"the postgres prose backend is unreachable: "
                f"{type(exc).__name__}") from exc

    # -- reads ---------------------------------------------------------

    def get(self, key: ProseKey) -> Prose:
        with self._tx() as cur:
            cur.execute(
                "select content, version, updated_at from sections "
                "where league_slug=%s and season=%s and issue_key=%s "
                "and kind=%s and section=%s",
                (key.league, key.season, key.issue, key.kind, key.name))
            row = cur.fetchone()
        if row is None:
            return Prose(key=key, text="", version=None)
        text, version, updated = row
        return Prose(key=key, text=text or "", version=token(version),
                     updated_at=updated.isoformat() if updated else None)

    def exists(self, key: ProseKey) -> bool:
        return self.get(key).exists

    def list_issue(self, league: str, season: str, issue: str, *,
                   kinds: tuple[str, ...] = KINDS) -> list[Prose]:
        with self._tx() as cur:
            cur.execute(
                "select kind, section, content, version, updated_at from sections "
                "where league_slug=%s and season=%s and issue_key=%s "
                "and kind = any(%s) order by kind, section",
                (league, season, issue, list(kinds)))
            rows = cur.fetchall()
        out = []
        for kind, name, text, version, updated in rows:
            out.append(Prose(key=ProseKey(league, season, issue, kind, name),
                             text=text or "", version=token(version),
                             updated_at=updated.isoformat() if updated else None))
        return out

    # -- writes --------------------------------------------------------

    def put(self, key: ProseKey, text: str, *, expected_version: str | None,
            source: str = "commissioner-save", keep_history: bool = True) -> Prose:
        expected = parse_token(expected_version)
        with self._tx() as cur:
            if expected is None:
                # Creation. `on conflict do nothing` makes the race a lost
                # insert rather than two rows or an exception, and a
                # rowcount of zero means somebody got there first.
                cur.execute(
                    "insert into sections (league_slug, season, issue_key, kind, "
                    "section, content, state, version, updated_at) "
                    "values (%s,%s,%s,%s,%s,%s,'generated',1, now()) "
                    "on conflict (league_slug, season, issue_key, kind, section) "
                    "do nothing returning version",
                    (key.league, key.season, key.issue, key.kind, key.name, text))
                row = cur.fetchone()
                if row is None:
                    current = self.get(key)
                    raise ProseConflict(key, expected_version, current.version,
                                        current.text)
                return Prose(key=key, text=text, version=token(row[0]))

            # Lock the row so the prior text this reads is the prior text
            # the update replaces. The update stays conditional regardless:
            # the lock orders writers, the guard decides.
            cur.execute(
                "select content from sections where league_slug=%s and season=%s "
                "and issue_key=%s and kind=%s and section=%s and version=%s "
                "for update",
                (key.league, key.season, key.issue, key.kind, key.name, expected))
            row = cur.fetchone()
            if row is None:
                current = self.get(key)
                raise ProseConflict(key, expected_version, current.version,
                                    current.text)
            prior = row[0] or ""
            if prior == text:
                return Prose(key=key, text=text, version=expected_version)
            if keep_history and prior and key.kind != PROPOSAL:
                cur.execute(
                    "insert into prose_revisions (league_slug, season, issue_key, "
                    "section, source, prior_text) values (%s,%s,%s,%s,%s,%s)",
                    (key.league, key.season, key.issue, key.section_id,
                     source, prior))
            cur.execute(
                "update sections set content=%s, version=version+1, "
                "updated_at=now() where league_slug=%s and season=%s "
                "and issue_key=%s and kind=%s and section=%s and version=%s "
                "returning version",
                (text, key.league, key.season, key.issue, key.kind, key.name,
                 expected))
            updated = cur.fetchone()
            if updated is None:                                 # pragma: no cover
                current = self.get(key)
                raise ProseConflict(key, expected_version, current.version,
                                    current.text)
        return Prose(key=key, text=text, version=token(updated[0]))

    def delete(self, key: ProseKey, *, expected_version: str | None = None) -> bool:
        expected = parse_token(expected_version)
        with self._tx() as cur:
            if expected is None:
                cur.execute(
                    "delete from sections where league_slug=%s and season=%s "
                    "and issue_key=%s and kind=%s and section=%s",
                    (key.league, key.season, key.issue, key.kind, key.name))
                return cur.rowcount > 0
            cur.execute(
                "delete from sections where league_slug=%s and season=%s "
                "and issue_key=%s and kind=%s and section=%s and version=%s",
                (key.league, key.season, key.issue, key.kind, key.name, expected))
            if cur.rowcount == 0:
                current = self.get(key)
                raise ProseConflict(key, expected_version, current.version,
                                    current.text)
            return True

    # -- history -------------------------------------------------------

    def history(self, key: ProseKey, limit: int = 10) -> list[dict]:
        with self._tx() as cur:
            cur.execute(
                "select id, source, prior_text, created_at from prose_revisions "
                "where league_slug=%s and season=%s and issue_key=%s "
                "and section=%s order by id desc limit %s",
                (key.league, key.season, key.issue, key.section_id, limit))
            rows = cur.fetchall()
        return [{"id": r[0], "source": r[1], "prior_text": r[2],
                 "created_at": r[3].isoformat() if r[3] else None} for r in rows]

    def revision_counts(self, league: str, season: str,
                        issue: str) -> dict[str, int]:
        with self._tx() as cur:
            cur.execute(
                "select section, count(*) from prose_revisions "
                "where league_slug=%s and season=%s and issue_key=%s "
                "group by section", (league, season, issue))
            return {r[0]: r[1] for r in cur.fetchall()}

    def revision(self, revision_id: int) -> dict | None:
        with self._tx() as cur:
            cur.execute(
                "select id, league_slug, season, issue_key, section, source, "
                "prior_text, created_at from prose_revisions where id=%s",
                (revision_id,))
            r = cur.fetchone()
        if r is None:
            return None
        return {"id": r[0], "league_slug": r[1], "season": r[2],
                "issue_key": r[3], "section": r[4], "source": r[5],
                "prior_text": r[6],
                "created_at": r[7].isoformat() if r[7] else None}

    # -- diagnostics ---------------------------------------------------

    def health(self) -> dict:
        """Safe facts only: no DSN, no host, no credential."""
        try:
            with self._tx() as cur:
                cur.execute("select count(*) from sections")
                rows = cur.fetchone()[0]
                cur.execute(
                    "select count(*) from information_schema.columns "
                    "where table_name='sections' and column_name='kind'")
                has_kind = cur.fetchone()[0] == 1
            return {"reachable": True, "schema_current": has_kind,
                    "prose_rows": rows}
        except ProseError as exc:
            return {"reachable": False, "schema_current": False,
                    "error": str(exc)[:200]}
