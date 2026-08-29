"""SQLite persistence for everything the League Page knows.

Same design as Fantasy Bot's storage: raw API payloads stored as JSON blobs
(so we never lose data to an incomplete schema) plus indexed columns for the
lookups we actually do, and a fetched_at timestamp so callers can decide
whether cached data is fresh enough to skip a re-fetch.

Beyond the Sleeper mirror this DB also holds the editorial layers:
  - archive_issues: imported historical newsletters, full-text searchable
  - takes: the receipts database (dated claims + eventual resolution)
  - bit_usage: when a recurring joke/storyline was last used, per manager
Editorial identity (managers, coalitions, recurring bits) lives in
editorial/*.json in git, not here — see docs/DECISIONS.md.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from leaguepage.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    full_name TEXT,
    position TEXT,
    team TEXT,
    status TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leagues (
    league_id TEXT PRIMARY KEY,
    name TEXT,
    season TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS league_users (
    league_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT,
    team_name TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS rosters (
    league_id TEXT NOT NULL,
    roster_id INTEGER NOT NULL,
    owner_id TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS matchups (
    league_id TEXT NOT NULL,
    week INTEGER NOT NULL,
    roster_id INTEGER NOT NULL,
    matchup_id INTEGER,
    points REAL,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    league_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    week INTEGER,
    type TEXT,
    status TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (league_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    league_id TEXT NOT NULL,
    season TEXT,
    status TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_picks (
    draft_id TEXT NOT NULL,
    pick_no INTEGER NOT NULL,
    round INTEGER,
    roster_id INTEGER,
    player_id TEXT,
    picked_by TEXT,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (draft_id, pick_no)
);

CREATE TABLE IF NOT EXISTS archive_issues (
    issue_id INTEGER PRIMARY KEY,
    league_slug TEXT NOT NULL,      -- "disco", "daddy", "surfeit", ...
    season TEXT,                    -- inferred NFL season start year; NULL if undated
    week INTEGER,                   -- NULL for draft/preseason/special issues
    title TEXT NOT NULL,            -- original doc title, verbatim
    source_path TEXT NOT NULL UNIQUE,  -- repo-relative path of the imported text
    body TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    doc_created TEXT,               -- source document creation timestamp
    doc_modified TEXT,              -- source document last-modified timestamp
    dating_confidence TEXT,         -- high | medium | low (season inference)
    dating_note TEXT                -- why the inference is what it is, if notable
);

CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
    title, body, content='archive_issues', content_rowid='issue_id'
);

CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON archive_issues BEGIN
    INSERT INTO archive_fts(rowid, title, body) VALUES (new.issue_id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON archive_issues BEGIN
    INSERT INTO archive_fts(archive_fts, rowid, title, body) VALUES ('delete', old.issue_id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS archive_au AFTER UPDATE ON archive_issues BEGIN
    INSERT INTO archive_fts(archive_fts, rowid, title, body) VALUES ('delete', old.issue_id, old.title, old.body);
    INSERT INTO archive_fts(rowid, title, body) VALUES (new.issue_id, new.title, new.body);
END;

CREATE TABLE IF NOT EXISTS takes (
    take_id INTEGER PRIMARY KEY,
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    week INTEGER,                   -- NULL for preseason/draft takes
    context TEXT,                   -- "preseason", "draft", "week-4", ...
    source TEXT NOT NULL,           -- "draft-review", "lowdown", "power", "matchup", ...
    author TEXT,                    -- who asserted it (default the commissioner)
    subject TEXT NOT NULL,          -- primary team slug or manager key
    players TEXT,                   -- JSON list of player names, optional
    topic TEXT,                     -- category: "rb-depth", "breakout", "draft-grade", ...
    quote TEXT NOT NULL,            -- original wording, verbatim — never edited
    confidence TEXT,                -- author's stated confidence, freeform
    status TEXT NOT NULL DEFAULT 'open',  -- open | validated | contradicted | retired | too_early
    resolution TEXT,                -- later evaluation; separate from the assertion
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS story_decisions (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    workflow TEXT NOT NULL,         -- "draft", "week-01", ...
    candidate_id TEXT NOT NULL,     -- stable id emitted by the story engine
    decision TEXT NOT NULL,         -- include | ignore | save
    note TEXT,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, workflow, candidate_id)
);

CREATE TABLE IF NOT EXISTS award_decisions (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    workflow TEXT NOT NULL,
    award_key TEXT NOT NULL,
    decision TEXT NOT NULL,         -- awarded | rejected | manual
    winner TEXT,                    -- team slug (or free text for manual winners)
    note TEXT,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, workflow, award_key)
);

CREATE TABLE IF NOT EXISTS power_rankings (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    label TEXT NOT NULL,            -- "preseason", "week-03", ...
    roster_id INTEGER NOT NULL,
    rank INTEGER,
    tier INTEGER,                   -- 1..4 per Peer and Near-Peer Competition
    note TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, label, roster_id)
);

CREATE TABLE IF NOT EXISTS issues (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    issue_key TEXT NOT NULL,        -- "draft", "week-01", ...
    status TEXT NOT NULL DEFAULT 'generated',  -- generated | edited | approved | published
    source_path TEXT,               -- markdown source under editorial/
    published_path TEXT,            -- rendered html under site/
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, issue_key)
);

CREATE TABLE IF NOT EXISTS bit_usage (
    usage_id INTEGER PRIMARY KEY,
    manager_key TEXT NOT NULL,      -- key into editorial/managers.json
    bit TEXT NOT NULL,              -- short label of the joke/storyline
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    week INTEGER,
    note TEXT,
    used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS editorial_usage (
    usage_id INTEGER PRIMARY KEY,
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    week INTEGER,
    matchup_slug TEXT,
    kind TEXT NOT NULL,             -- angle | frame | callback | joke_family | bit
    value TEXT NOT NULL,            -- e.g. "gripen-procurement", "archive:issue:12"
    note TEXT,
    used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_names (
    league_slug TEXT NOT NULL,
    roster_id INTEGER NOT NULL,
    public_name TEXT NOT NULL,      -- commissioner-confirmed public display name
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, roster_id)
);

CREATE TABLE IF NOT EXISTS issue_modules (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    issue_key TEXT NOT NULL,        -- "draft", "week-01", ...
    module_key TEXT NOT NULL,       -- "lowdown", "ctp", "awards", ...
    position INTEGER,
    included INTEGER NOT NULL DEFAULT 1,
    custom_title TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, issue_key, module_key)
);

CREATE TABLE IF NOT EXISTS matchup_state (
    league_slug TEXT NOT NULL,
    season TEXT NOT NULL,
    week INTEGER NOT NULL,
    matchup_slug TEXT NOT NULL,
    selected_angle_id TEXT,
    custom_angle TEXT,              -- commissioner-written premise, overrides selection
    angle_note TEXT,
    prominence_override TEXT,       -- FEATURE | MAJOR | STANDARD | CAPSULE
    status TEXT NOT NULL DEFAULT 'packet_ready',
        -- packet_ready | angle_needed | ready_to_draft | drafted | edited
        -- | approved | locked | rejected
    revision_requests TEXT,         -- JSON list of structured requests for next pass
    updated_at TEXT NOT NULL,
    PRIMARY KEY (league_slug, season, week, matchup_slug)
);
"""


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a table already existed on disk.
        CREATE TABLE IF NOT EXISTS won't alter existing tables, so new columns
        are added here; safe to run every startup."""
        added = {
            "archive_issues": ["doc_created TEXT", "doc_modified TEXT",
                               "dating_confidence TEXT", "dating_note TEXT"],
            "takes": ["context TEXT", "author TEXT", "players TEXT", "topic TEXT"],
            "story_decisions": ["route TEXT"],  # lowdown | matchup | award | blackbox | custom
            "issues": ["theme TEXT"],           # optional issue-wide gimmick
        }
        for table, columns in added.items():
            existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for col in columns:
                if col.split()[0] not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # -- meta ----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -- players -------------------------------------------------------

    def players_last_updated(self) -> dt.datetime | None:
        raw = self.get_meta("players_updated_at")
        return dt.datetime.fromisoformat(raw) if raw else None

    def save_players(self, players: dict[str, dict]) -> None:
        now = utcnow_iso()
        rows = []
        for player_id, p in players.items():
            full_name = p.get("full_name") or " ".join(
                filter(None, [p.get("first_name"), p.get("last_name")])
            )
            rows.append(
                (player_id, full_name or None, p.get("position"), p.get("team"),
                 p.get("status"), json.dumps(p), now)
            )
        with self._cursor() as cur:
            cur.executemany(
                "INSERT INTO players (player_id, full_name, position, team, status, data, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(player_id) DO UPDATE SET "
                "full_name=excluded.full_name, position=excluded.position, team=excluded.team, "
                "status=excluded.status, data=excluded.data, updated_at=excluded.updated_at",
                rows,
            )
        self.set_meta("players_updated_at", now)

    def get_player(self, player_id: str) -> dict | None:
        row = self._conn.execute("SELECT data FROM players WHERE player_id = ?", (player_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def player_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]

    # -- leagues -------------------------------------------------------

    def save_league(self, league_id: str, data: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO leagues (league_id, name, season, data, fetched_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id) DO UPDATE SET "
                "name=excluded.name, season=excluded.season, data=excluded.data, fetched_at=excluded.fetched_at",
                (league_id, data.get("name"), data.get("season"), json.dumps(data), utcnow_iso()),
            )

    def get_league(self, league_id: str) -> dict | None:
        row = self._conn.execute("SELECT data FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    # -- league users --------------------------------------------------

    def save_league_users(self, league_id: str, users: list[dict]) -> None:
        now = utcnow_iso()
        rows = [
            (league_id, u["user_id"], u.get("display_name"),
             (u.get("metadata") or {}).get("team_name"), json.dumps(u), now)
            for u in users
        ]
        with self._cursor() as cur:
            cur.executemany(
                "INSERT INTO league_users (league_id, user_id, display_name, team_name, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id, user_id) DO UPDATE SET "
                "display_name=excluded.display_name, team_name=excluded.team_name, "
                "data=excluded.data, fetched_at=excluded.fetched_at",
                rows,
            )

    def get_league_users(self, league_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM league_users WHERE league_id = ?", (league_id,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- rosters -------------------------------------------------------

    def save_rosters(self, league_id: str, rosters: list[dict]) -> None:
        now = utcnow_iso()
        rows = [(league_id, r["roster_id"], r.get("owner_id"), json.dumps(r), now) for r in rosters]
        with self._cursor() as cur:
            cur.execute("DELETE FROM rosters WHERE league_id = ?", (league_id,))
            cur.executemany(
                "INSERT INTO rosters (league_id, roster_id, owner_id, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def get_rosters(self, league_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM rosters WHERE league_id = ? ORDER BY roster_id", (league_id,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- matchups ------------------------------------------------------

    def save_matchups(self, league_id: str, week: int, matchups: list[dict]) -> None:
        now = utcnow_iso()
        rows = [
            (league_id, week, m["roster_id"], m.get("matchup_id"), m.get("points"), json.dumps(m), now)
            for m in matchups
        ]
        with self._cursor() as cur:
            cur.execute("DELETE FROM matchups WHERE league_id = ? AND week = ?", (league_id, week))
            cur.executemany(
                "INSERT INTO matchups (league_id, week, roster_id, matchup_id, points, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM matchups WHERE league_id = ? AND week = ?", (league_id, week)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- transactions --------------------------------------------------

    def save_transactions(self, league_id: str, week: int, transactions: list[dict]) -> None:
        now = utcnow_iso()
        rows = [
            (league_id, t["transaction_id"], t.get("leg", week), t.get("type"),
             t.get("status"), json.dumps(t), now)
            for t in transactions
        ]
        with self._cursor() as cur:
            cur.executemany(
                "INSERT INTO transactions (league_id, transaction_id, week, type, status, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id, transaction_id) DO UPDATE SET "
                "week=excluded.week, type=excluded.type, status=excluded.status, "
                "data=excluded.data, fetched_at=excluded.fetched_at",
                rows,
            )

    def get_transactions(self, league_id: str, week: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM transactions WHERE league_id = ? AND week = ?", (league_id, week)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- drafts --------------------------------------------------------

    def save_draft(self, draft: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO drafts (draft_id, league_id, season, status, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(draft_id) DO UPDATE SET "
                "league_id=excluded.league_id, season=excluded.season, status=excluded.status, "
                "data=excluded.data, fetched_at=excluded.fetched_at",
                (draft["draft_id"], draft.get("league_id"), draft.get("season"),
                 draft.get("status"), json.dumps(draft), utcnow_iso()),
            )

    def get_drafts_for_league(self, league_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM drafts WHERE league_id = ?", (league_id,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_draft_picks(self, draft_id: str, picks: list[dict]) -> None:
        now = utcnow_iso()
        rows = [
            (draft_id, p["pick_no"], p.get("round"), p.get("roster_id"),
             p.get("player_id"), p.get("picked_by"), json.dumps(p), now)
            for p in picks
        ]
        with self._cursor() as cur:
            cur.execute("DELETE FROM draft_picks WHERE draft_id = ?", (draft_id,))
            cur.executemany(
                "INSERT INTO draft_picks (draft_id, pick_no, round, roster_id, player_id, picked_by, data, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM draft_picks WHERE draft_id = ? ORDER BY pick_no", (draft_id,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- archive -------------------------------------------------------

    def upsert_archive_issue(
        self,
        *,
        league_slug: str,
        season: str | None,
        week: int | None,
        title: str,
        source_path: str,
        body: str,
        doc_created: str | None = None,
        doc_modified: str | None = None,
        dating_confidence: str | None = None,
        dating_note: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT issue_id FROM archive_issues WHERE source_path = ?", (source_path,)
            ).fetchone()
            if row:
                cur.execute(
                    "UPDATE archive_issues SET league_slug=?, season=?, week=?, title=?, body=?, "
                    "imported_at=?, doc_created=?, doc_modified=?, dating_confidence=?, dating_note=? "
                    "WHERE issue_id=?",
                    (league_slug, season, week, title, body, utcnow_iso(),
                     doc_created, doc_modified, dating_confidence, dating_note, row["issue_id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO archive_issues (league_slug, season, week, title, source_path, body, "
                    "imported_at, doc_created, doc_modified, dating_confidence, dating_note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_slug, season, week, title, source_path, body, utcnow_iso(),
                     doc_created, doc_modified, dating_confidence, dating_note),
                )

    def archive_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) c FROM archive_issues").fetchone()["c"]

    def list_archive_issues(self, league_slug: str | None = None) -> list[dict]:
        q = ("SELECT issue_id, league_slug, season, week, title, source_path, "
             "doc_created, doc_modified, dating_confidence, dating_note FROM archive_issues")
        params: tuple = ()
        if league_slug:
            q += " WHERE league_slug = ?"
            params = (league_slug,)
        q += " ORDER BY league_slug, season, week"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def search_archive(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across imported newsletters. Returns snippets."""
        rows = self._conn.execute(
            "SELECT a.issue_id, a.league_slug, a.season, a.week, a.title, "
            "snippet(archive_fts, 1, '[', ']', ' … ', 20) AS snippet "
            "FROM archive_fts JOIN archive_issues a ON a.issue_id = archive_fts.rowid "
            "WHERE archive_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_archive_issue(self, issue_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM archive_issues WHERE issue_id = ?", (issue_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- takes (receipts) ----------------------------------------------
    # A take stores the exact original wording forever; later evaluation goes
    # in status/resolution so assertion and judgment stay separately auditable.

    TAKE_STATUSES = ("open", "validated", "contradicted", "retired", "too_early")

    def add_take(
        self,
        *,
        league_slug: str,
        season: str,
        week: int | None,
        source: str,
        subject: str,
        quote: str,
        context: str | None = None,
        author: str | None = None,
        players: list[str] | None = None,
        topic: str | None = None,
        confidence: str | None = None,
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO takes (league_slug, season, week, context, source, author, subject, "
                "players, topic, quote, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (league_slug, season, week, context, source, author, subject,
                 json.dumps(players) if players else None, topic, quote, confidence, utcnow_iso()),
            )
            return cur.lastrowid

    def resolve_take(self, take_id: int, status: str, resolution: str | None = None) -> None:
        if status not in self.TAKE_STATUSES or status == "open":
            raise ValueError(f"Invalid take status {status!r}")
        with self._cursor() as cur:
            cur.execute(
                "UPDATE takes SET status=?, resolution=?, resolved_at=? WHERE take_id=?",
                (status, resolution, utcnow_iso(), take_id),
            )

    def open_takes(self, league_slug: str) -> list[dict]:
        """Takes still awaiting a verdict — 'too_early' means looked at, not settled."""
        rows = self._conn.execute(
            "SELECT * FROM takes WHERE league_slug = ? AND status IN ('open', 'too_early') "
            "ORDER BY created_at",
            (league_slug,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_takes(self, league_slug: str, season: str | None = None) -> list[dict]:
        q = "SELECT * FROM takes WHERE league_slug = ?"
        params: list = [league_slug]
        if season:
            q += " AND season = ?"
            params.append(season)
        q += " ORDER BY created_at"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    # -- commissioner decisions ----------------------------------------

    def set_story_decision(
        self, *, league_slug: str, season: str, workflow: str, candidate_id: str,
        decision: str, note: str | None = None, route: str | None = None,
    ) -> None:
        if decision not in ("include", "ignore", "save"):
            raise ValueError(f"Invalid story decision {decision!r}")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO story_decisions (league_slug, season, workflow, candidate_id, decision, note, route, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_slug, season, workflow, candidate_id) DO UPDATE SET "
                "decision=excluded.decision, note=excluded.note, "
                "route=COALESCE(excluded.route, route), decided_at=excluded.decided_at",
                (league_slug, season, workflow, candidate_id, decision, note, route, utcnow_iso()),
            )

    # -- confirmed public team names (5.1B safeguard) -------------------

    def set_public_team_name(self, league_slug: str, roster_id: int, public_name: str) -> None:
        name = public_name.strip()
        if not name:
            raise ValueError("public_name must be non-empty")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO team_names (league_slug, roster_id, public_name, confirmed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(league_slug, roster_id) DO UPDATE SET "
                "public_name=excluded.public_name, confirmed_at=excluded.confirmed_at",
                (league_slug, roster_id, name, utcnow_iso()),
            )

    def get_public_team_names(self, league_slug: str) -> dict[int, str]:
        rows = self._conn.execute(
            "SELECT roster_id, public_name FROM team_names WHERE league_slug=?",
            (league_slug,),
        ).fetchall()
        return {r["roster_id"]: r["public_name"] for r in rows}

    # -- issue modules (Issue Builder) ---------------------------------

    def set_issue_module(
        self, *, league_slug: str, season: str, issue_key: str, module_key: str, **fields,
    ) -> None:
        allowed = {"position", "included", "custom_title", "approved"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unknown issue module fields: {bad}")
        now = utcnow_iso()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM issue_modules WHERE league_slug=? AND season=? AND issue_key=? AND module_key=?",
                (league_slug, season, issue_key, module_key),
            ).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in fields)
                cur.execute(
                    f"UPDATE issue_modules SET {sets}, updated_at=? "
                    "WHERE league_slug=? AND season=? AND issue_key=? AND module_key=?",
                    (*fields.values(), now, league_slug, season, issue_key, module_key),
                )
            else:
                cols = ["league_slug", "season", "issue_key", "module_key", *fields, "updated_at"]
                cur.execute(
                    f"INSERT INTO issue_modules ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (league_slug, season, issue_key, module_key, *fields.values(), now),
                )

    def get_issue_modules(self, league_slug: str, season: str, issue_key: str) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM issue_modules WHERE league_slug=? AND season=? AND issue_key=?",
            (league_slug, season, issue_key),
        ).fetchall()
        return {r["module_key"]: dict(r) for r in rows}

    def set_issue_theme(self, league_slug: str, season: str, issue_key: str, theme: str | None) -> None:
        self.set_issue_status(league_slug=league_slug, season=season, issue_key=issue_key,
                              status=(self.get_issue(league_slug, season, issue_key) or {}).get("status", "generated"))
        with self._cursor() as cur:
            cur.execute(
                "UPDATE issues SET theme=?, updated_at=? WHERE league_slug=? AND season=? AND issue_key=?",
                (theme, utcnow_iso(), league_slug, season, issue_key),
            )

    def get_story_decisions(self, league_slug: str, season: str, workflow: str) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM story_decisions WHERE league_slug=? AND season=? AND workflow=?",
            (league_slug, season, workflow),
        ).fetchall()
        return {r["candidate_id"]: dict(r) for r in rows}

    def set_award_decision(
        self, *, league_slug: str, season: str, workflow: str, award_key: str,
        decision: str, winner: str | None = None, note: str | None = None,
    ) -> None:
        if decision not in ("awarded", "rejected", "manual"):
            raise ValueError(f"Invalid award decision {decision!r}")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO award_decisions (league_slug, season, workflow, award_key, decision, winner, note, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_slug, season, workflow, award_key) DO UPDATE SET "
                "decision=excluded.decision, winner=excluded.winner, note=excluded.note, decided_at=excluded.decided_at",
                (league_slug, season, workflow, award_key, decision, winner, note, utcnow_iso()),
            )

    def get_award_decisions(self, league_slug: str, season: str, workflow: str) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM award_decisions WHERE league_slug=? AND season=? AND workflow=?",
            (league_slug, season, workflow),
        ).fetchall()
        return {r["award_key"]: dict(r) for r in rows}

    # -- power rankings (commissioner-owned) ---------------------------

    def save_power_rankings(self, league_slug: str, season: str, label: str, entries: list[dict]) -> None:
        """entries: [{roster_id, rank, tier, note}] — replaces the whole label."""
        now = utcnow_iso()
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM power_rankings WHERE league_slug=? AND season=? AND label=?",
                (league_slug, season, label),
            )
            cur.executemany(
                "INSERT INTO power_rankings (league_slug, season, label, roster_id, rank, tier, note, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (league_slug, season, label, e["roster_id"], e.get("rank"),
                     e.get("tier"), e.get("note"), now)
                    for e in entries
                ],
            )

    def get_power_rankings(self, league_slug: str, season: str, label: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM power_rankings WHERE league_slug=? AND season=? AND label=? "
            "ORDER BY rank IS NULL, rank",
            (league_slug, season, label),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- issue lifecycle -----------------------------------------------

    ISSUE_STATUSES = ("generated", "edited", "approved", "published")

    def set_issue_status(
        self, *, league_slug: str, season: str, issue_key: str, status: str,
        source_path: str | None = None, published_path: str | None = None,
    ) -> None:
        if status not in self.ISSUE_STATUSES:
            raise ValueError(f"Invalid issue status {status!r}")
        now = utcnow_iso()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM issues WHERE league_slug=? AND season=? AND issue_key=?",
                (league_slug, season, issue_key),
            ).fetchone()
            if row:
                cur.execute(
                    "UPDATE issues SET status=?, source_path=COALESCE(?, source_path), "
                    "published_path=COALESCE(?, published_path), updated_at=? "
                    "WHERE league_slug=? AND season=? AND issue_key=?",
                    (status, source_path, published_path, now, league_slug, season, issue_key),
                )
            else:
                cur.execute(
                    "INSERT INTO issues (league_slug, season, issue_key, status, source_path, published_path, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_slug, season, issue_key, status, source_path, published_path, now, now),
                )

    def get_issue(self, league_slug: str, season: str, issue_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM issues WHERE league_slug=? AND season=? AND issue_key=?",
            (league_slug, season, issue_key),
        ).fetchone()
        return dict(row) if row else None

    # -- bit usage -----------------------------------------------------

    def log_bit_usage(
        self,
        *,
        manager_key: str,
        bit: str,
        league_slug: str,
        season: str,
        week: int | None,
        note: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO bit_usage (manager_key, bit, league_slug, season, week, note, used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (manager_key, bit, league_slug, season, week, note, utcnow_iso()),
            )

    def recent_bit_usage(self, manager_key: str, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM bit_usage WHERE manager_key = ? ORDER BY used_at DESC LIMIT ?",
            (manager_key, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- editorial usage (repetition control) --------------------------

    EDITORIAL_USAGE_KINDS = ("angle", "frame", "callback", "joke_family", "bit")

    def log_editorial_usage(
        self, *, league_slug: str, season: str, week: int | None,
        kind: str, value: str, matchup_slug: str | None = None, note: str | None = None,
    ) -> None:
        if kind not in self.EDITORIAL_USAGE_KINDS:
            raise ValueError(f"Invalid editorial usage kind {kind!r}")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO editorial_usage (league_slug, season, week, matchup_slug, kind, value, note, used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (league_slug, season, week, matchup_slug, kind, value, note, utcnow_iso()),
            )

    def recent_editorial_usage(
        self, league_slug: str, season: str, *,
        since_week: int | None = None, kind: str | None = None,
    ) -> list[dict]:
        """Usage log for a league-season, newest first. In-league only by
        design: repetition in one league never suppresses the other's jokes."""
        q = "SELECT * FROM editorial_usage WHERE league_slug=? AND season=?"
        params: list = [league_slug, season]
        if since_week is not None:
            q += " AND (week IS NULL OR week >= ?)"
            params.append(since_week)
        if kind:
            q += " AND kind=?"
            params.append(kind)
        q += " ORDER BY week DESC, used_at DESC"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def usage_count(self, league_slug: str, season: str, kind: str, value: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) c FROM editorial_usage WHERE league_slug=? AND season=? AND kind=? AND value=?",
            (league_slug, season, kind, value),
        ).fetchone()["c"]

    # -- matchup workflow state ----------------------------------------

    MATCHUP_STATUSES = ("packet_ready", "angle_needed", "ready_to_draft", "drafted",
                        "edited", "approved", "locked", "rejected")

    def set_matchup_state(
        self, *, league_slug: str, season: str, week: int, matchup_slug: str, **fields,
    ) -> None:
        """Partial update; only provided fields change. Creates the row if new."""
        allowed = {"selected_angle_id", "custom_angle", "angle_note",
                   "prominence_override", "status", "revision_requests"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unknown matchup_state fields: {bad}")
        if "status" in fields and fields["status"] not in self.MATCHUP_STATUSES:
            raise ValueError(f"Invalid matchup status {fields['status']!r}")
        if "revision_requests" in fields and not isinstance(fields["revision_requests"], (str, type(None))):
            fields["revision_requests"] = json.dumps(fields["revision_requests"])
        now = utcnow_iso()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM matchup_state WHERE league_slug=? AND season=? AND week=? AND matchup_slug=?",
                (league_slug, season, week, matchup_slug),
            ).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in fields)
                cur.execute(
                    f"UPDATE matchup_state SET {sets}, updated_at=? "
                    "WHERE league_slug=? AND season=? AND week=? AND matchup_slug=?",
                    (*fields.values(), now, league_slug, season, week, matchup_slug),
                )
            else:
                cols = ["league_slug", "season", "week", "matchup_slug", *fields, "updated_at"]
                cur.execute(
                    f"INSERT INTO matchup_state ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (league_slug, season, week, matchup_slug, *fields.values(), now),
                )

    def get_matchup_state(self, league_slug: str, season: str, week: int, matchup_slug: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM matchup_state WHERE league_slug=? AND season=? AND week=? AND matchup_slug=?",
            (league_slug, season, week, matchup_slug),
        ).fetchone()
        return dict(row) if row else None

    def list_matchup_states(self, league_slug: str, season: str, week: int) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM matchup_state WHERE league_slug=? AND season=? AND week=?",
            (league_slug, season, week),
        ).fetchall()
        return {r["matchup_slug"]: dict(r) for r in rows}
