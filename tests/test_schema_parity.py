"""Does the Postgres target schema still cover the editorial state?

A static check, deliberately: it needs no database, no credential and no
network, so it runs in the ordinary suite and answers the question the
live verifier cannot. `scripts/verify_supabase_schema.py` asks a real
Supabase whether the migrations landed. This asks whether the migrations
would be *enough* if they did.

The answer today is no, and the gaps are declared below rather than
hidden. That is the point: a gap nobody has written down is a gap
somebody discovers during a cutover.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "migrations"

# The SQLite tables a hosted Desk needs. The Sleeper cache (players,
# leagues, rosters, matchups, transactions, drafts, draft_picks) and the
# local archive index are deliberately absent: a hosted Desk re-syncs
# those from Sleeper and re-indexes the archive, so they never migrate.
EDITORIAL_TABLES = {
    "issues", "issue_modules", "issue_revision_requests", "matchup_state",
    "prose_revisions", "section_prose_state", "prose_provenance",
    "force_flow_notes", "team_names", "story_decisions", "award_decisions",
    "power_rankings", "takes", "bit_usage", "editorial_usage",
    "sync_snapshots",
}

# Known and accepted, each with the reason it is still open. This set is
# the deliverable: when the unified-cloud-editorial-state tranche lands,
# entries come OUT of here and the test keeps passing. If an entry has to
# go IN, that is a new gap and it belongs in the architecture doc first.
KNOWN_MISSING_TABLES = {
    "section_prose_state":
        "generated vs commissioner-edited. Postgres carries this on "
        "sections.state instead, so the gap is a CALLER not a table: the "
        "Desk writes set_prose_state() to SQLite while the repository "
        "writes only content and version. Measured live 2026-09-06: 28 "
        "sections are commissioner-edited in SQLite and generated in "
        "Postgres.",
    "prose_provenance":
        "the authorship claim; written after every prose write",
    "force_flow_notes":
        "per-transaction Commissioner blurbs; outside the prose boundary",
}

# Columns a hosted Desk will never want. `issues` gained `published_at` in
# Postgres in their place, which is the right shape: a hosted Desk has no
# filesystem paths to record.
DELIBERATELY_NOT_MIGRATED = {
    ("issues", "source_path"): "a path under editorial/ on this machine",
    ("issues", "published_path"): "a path under site/ on this machine",
}

KNOWN_MISSING_COLUMNS = {
    ("issue_modules", "approved_sha"):
        "the ONLY content-bound approval in the system (CTP) has no home in "
        "the target schema; added to SQLite additively on 2026-09-05",
    ("issues", "theme"):
        "the optional issue-wide gimmick; editorial intent, not a derivation",
    ("matchup_state", "revision_requests"):
        "structured requests carried into the next drafting pass",
}


def _sqlite_tables() -> dict[str, set[str]]:
    """{table: columns} from the authoritative SQLite schema, including the
    columns added additively after a table already existed on disk."""
    from leaguepage import storage

    tables: dict[str, set[str]] = {}
    for block in re.finditer(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);",
            storage.SCHEMA, re.S | re.I):
        tables[block.group(1)] = _columns(block.group(2))
    for table, columns in _additive_columns().items():
        if table in tables:
            tables[table] |= {c.split()[0] for c in columns}
    return tables


def _additive_columns() -> dict[str, list[str]]:
    """The `added` dict inside Storage._migrate, read from the source.

    Reading the source rather than calling the method keeps this test free
    of a database, which is the whole point of a static check.
    """
    from leaguepage import storage

    src = Path(storage.__file__).read_text(encoding="utf-8")
    body = src[src.index("        added = {"):]
    body = body[:body.index("\n        for table, columns in added.items():")]
    return eval(body.strip().removeprefix("added = "))  # noqa: S307 - our own source


def _postgres_tables() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for block in re.finditer(
                r"create table if not exists\s+(\w+)\s*\((.*?)\n\);",
                sql, re.S | re.I):
            tables.setdefault(block.group(1), set())
            tables[block.group(1)] |= _columns(block.group(2))
        # later migrations add columns to earlier tables
        for table, column in re.findall(
                r"alter table\s+(?:if exists\s+)?(\w+)\s+add column\s+"
                r"(?:if not exists\s+)?(\w+)", sql, re.I):
            tables.setdefault(table, set()).add(column)
        for table, old, new in re.findall(
                r"alter table\s+(?:if exists\s+)?(\w+)\s+rename column\s+"
                r"(\w+)\s+to\s+(\w+)", sql, re.I):
            tables.setdefault(table, set()).discard(old)
            tables[table].add(new)
    return tables


CONSTRAINTS = {"primary", "unique", "foreign", "constraint", "check", "--", ")"}


def _columns(body: str) -> set[str]:
    out = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        first = line.split()[0].strip(",").lower()
        if first in CONSTRAINTS or not re.fullmatch(r"\w+", first):
            continue
        out.add(first)
    return out


def test_every_editorial_table_named_here_exists_in_sqlite():
    """The list above is checked against reality in both directions, so a
    renamed table cannot silently drop out of the comparison."""
    missing = EDITORIAL_TABLES - set(_sqlite_tables())
    assert missing == set(), missing


def test_the_postgres_schema_covers_the_editorial_tables_or_says_why_not():
    sqlite, postgres = _sqlite_tables(), _postgres_tables()
    gap = {t for t in EDITORIAL_TABLES if t not in postgres}
    assert gap == set(KNOWN_MISSING_TABLES), (
        f"undeclared: {sorted(gap - set(KNOWN_MISSING_TABLES))}; "
        f"fixed, remove from KNOWN_MISSING_TABLES: "
        f"{sorted(set(KNOWN_MISSING_TABLES) - gap)}")
    assert sqlite  # the parser found something


def test_the_shared_tables_have_the_same_columns_or_say_why_not():
    """Column drift is the quieter failure: the table is there, the write
    lands, and one field of the Commissioner's intent is gone."""
    sqlite, postgres = _sqlite_tables(), _postgres_tables()
    gap = set()
    for table in sorted(EDITORIAL_TABLES & set(postgres)):
        for column in sorted(sqlite.get(table, set()) - postgres[table]):
            if (table, column) not in DELIBERATELY_NOT_MIGRATED:
                gap.add((table, column))
    assert gap == set(KNOWN_MISSING_COLUMNS), (
        f"undeclared: {sorted(gap - set(KNOWN_MISSING_COLUMNS))}; "
        f"fixed, remove from KNOWN_MISSING_COLUMNS: "
        f"{sorted(set(KNOWN_MISSING_COLUMNS) - gap)}")


def test_the_staleness_flags_have_nowhere_to_go_yet():
    """`_changed_since_approval` reads `meta` with a raw LIKE through
    `s._conn`. Postgres has `editorial_meta`, so the table exists -- but
    nothing routes that read to it, and a prefix scan is not in the
    repository contract. Pinned because it is easy to believe this one is
    already handled: the table's existence is not the same as a caller."""
    src = (REPO / "leaguepage" / "desk_editor.py").read_text(encoding="utf-8")
    assert "SELECT key FROM meta WHERE key LIKE ?" in src
    assert "editorial_meta" not in src
