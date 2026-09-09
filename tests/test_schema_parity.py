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

# Known and accepted, each with the reason it is still open. This set was
# the deliverable of the audit tranche and 0006 emptied most of it: entries
# come OUT as they are closed. If an entry has to go IN, that is a new gap
# and it belongs in the architecture doc first.
KNOWN_MISSING_TABLES = {
    "section_prose_state":
        "DELIBERATE AND PERMANENT. Postgres carries this meaning on "
        "sections.state, so the gap was never a missing table -- it was a "
        "missing caller: the Desk wrote set_prose_state() to SQLite while "
        "the repository wrote only content and version. Tranche 5B fixes "
        "the caller. This entry should never be removed by adding a table.",
}

# Columns a hosted Desk will never want. `issues` gained `published_at` in
# Postgres in their place, which is the right shape: a hosted Desk has no
# filesystem paths to record.
DELIBERATELY_NOT_MIGRATED = {
    ("issues", "source_path"): "a path under editorial/ on this machine",
    ("issues", "published_path"): "a path under site/ on this machine",
}

# Empty, and it should stay empty. 0006 closed all four.
KNOWN_MISSING_COLUMNS: dict[tuple[str, str], str] = {}

# Tables that exist ONLY in Postgres, each with the reason there is nothing
# on the SQLite side to compare them to. Declared for the same reason the
# gaps above are: a table nobody has written down is a table somebody
# discovers during a cutover.
POSTGRES_ONLY_TABLES = {
    "app_commissioners":
        "the allowlist RLS reads. A local Desk has no RLS and no sign-in.",
    "editorial_meta":
        "SQLite calls the same thing `meta`. Recomputable settings; the "
        "import deliberately does not compare it.",
    "sections":
        "the prose tree. On this machine that IS the filesystem -- a "
        "directory of Markdown -- so there is no table to line it up with.",
    "research_artifacts":
        "research that arrives from outside the Desk. On this machine it "
        "is a file under editorial/; 0006 gave it a home a hosted Desk "
        "can reach.",
    "site_documents":
        "site-wide authored copy with no league, season or issue. On this "
        "machine About is editorial/site/about.md; 0007 gave it a home a "
        "hosted Desk can reach. Same shape as research_artifacts: a "
        "filesystem thing that needed a table, not a table that needed a "
        "filesystem.",
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


def test_zero_six_closed_the_gaps_it_was_written_to_close():
    """The specific claim the migration makes, checked rather than trusted.

    Written as its own test because "the declared set is empty" reads as an
    absence and this reads as a presence: these are the four columns and
    three tables that Tranche 5B needed, and they are here.
    """
    postgres = _postgres_tables()
    for table in ("prose_provenance", "force_flow_notes", "research_artifacts"):
        assert table in postgres, f"0006 should create {table}"
    for table, column in (("issue_modules", "approved_sha"),
                          ("issues", "theme"),
                          ("matchup_state", "revision_requests"),
                          ("matchup_state", "covered_sha")):
        assert column in postgres[table], f"0006 should add {table}.{column}"
    assert "section_prose_state" not in postgres, (
        "sections.state already carries this; adding a table would be the "
        "wrong fix for a missing caller")
    assert "state" in postgres["sections"]


def test_zero_six_locks_down_everything_it_creates():
    """A new table with no policy is a readable table. The migration uses
    the same `do $$` block 0001 uses, so this checks the block covers every
    table the migration created rather than most of them."""
    sql = (MIGRATIONS / "0006_editorial_state.sql").read_text(encoding="utf-8")
    created = set(re.findall(r"create table if not exists\s+(\w+)", sql, re.I))
    locked = set()
    for block in re.findall(r"foreach t in array array\[(.*?)\]", sql, re.S):
        locked |= {m.strip().strip("'") for m in block.split(",")}
    assert created <= locked, f"not locked down: {sorted(created - locked)}"
    for needed in ("enable row level security", "force row level security",
                   "create policy commissioner_all",
                   "revoke all on function app_is_commissioner() from public"):
        assert needed in sql.lower(), f"0006 is missing: {needed}"


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


def test_the_staleness_flags_are_gone_rather_than_migrated():
    """This gap closed by deletion, which is the better way to close one.

    "Changed since approval" used to be a `meta` row that every mutating
    path had to set and every approval had to clear, read back with a raw
    LIKE through `s._conn` -- a prefix scan no repository contract has,
    and a flag a crash could leave describing the wrong text. It is now
    the approval signature failing to match the prose, which cannot be
    out of step with the prose by construction.

    So there is nothing left to migrate, and this test exists to make
    sure nobody reintroduces the flag while porting the feature.
    """
    src = (REPO / "leaguepage" / "desk_editor.py").read_text(encoding="utf-8")
    assert "SELECT key FROM meta WHERE key LIKE ?" not in src
    assert "approval-stale:" not in src, "the meta key namespace is retired"
    assert "_mark_changed" not in src, "nothing marks staleness any more"
    assert "approval_stale" in src, "it is derived from the signature"


def test_every_postgres_only_table_is_declared():
    """Both directions, like the SQLite list above: a table that appears in
    a migration with no counterpart here is an undeclared difference, and a
    declaration whose table has gone is a stale one."""
    only = set(_postgres_tables()) - set(_sqlite_tables())
    assert only == set(POSTGRES_ONLY_TABLES), (
        f"undeclared: {sorted(only - set(POSTGRES_ONLY_TABLES))}; "
        f"gone, remove the declaration: "
        f"{sorted(set(POSTGRES_ONLY_TABLES) - only)}")


# ------------------------------------------------------------------- 0007

ZERO_SEVEN = MIGRATIONS / "0007_site_documents.sql"


def test_zero_seven_creates_the_site_document_table():
    """The last authoring surface that wrote to this machine's filesystem.

    Site -> About persisted to editorial/site/about.md and site_build read
    the same file, so a hosted Desk would have discarded the Commissioner's
    own words about how the paper is made.
    """
    postgres = _postgres_tables()
    assert "site_documents" in postgres, "0007 should create site_documents"
    assert postgres["site_documents"] == {
        "slug", "body", "updated_at", "updated_by"}


def test_zero_seven_does_not_invent_a_prose_key_for_a_page_that_has_none():
    """The failure this migration exists to avoid.

    About has no league, no season and no issue. The cheap way to store it
    was a row in `sections` under made-up values, and `sections` is the
    table every readiness count, diff and parity check is addressed by.
    """
    sql = ZERO_SEVEN.read_text(encoding="utf-8")
    body = re.search(r"create table if not exists\s+site_documents\s*\((.*?)\n\);",
                     sql, re.S | re.I).group(1)
    for invented in ("league_slug", "season", "issue_key", "kind", "section"):
        assert invented not in body, (
            f"site_documents should not carry {invented}: About is not "
            f"scoped to an issue and pretending otherwise puts a lie in a "
            f"primary key")
    for wrong_home in ("research_artifacts", "editorial_meta",
                       "prose_revisions"):
        assert f"into {wrong_home}" not in sql.lower()


def test_zero_seven_is_additive_and_idempotent():
    """It is pasted into a SQL editor by hand, so running it twice has to be
    a no-op rather than a question."""
    sql = ZERO_SEVEN.read_text(encoding="utf-8")
    assert "create table if not exists site_documents" in sql.lower()
    assert "drop policy if exists commissioner_all" in sql.lower()
    for destructive in ("drop table", "delete from", "truncate",
                        "drop column", "alter column"):
        assert destructive not in sql.lower(), (
            f"0007 must not {destructive}: it runs against production data")


def test_zero_seven_locks_down_everything_it_creates():
    """Same check 0006 gets, for the same reason: a new table with no policy
    is a readable table."""
    sql = ZERO_SEVEN.read_text(encoding="utf-8")
    created = set(re.findall(r"create table if not exists\s+(\w+)", sql, re.I))
    locked = set()
    for block in re.findall(r"foreach t in array array\[(.*?)\]", sql, re.S):
        locked |= {m.strip().strip("'") for m in block.split(",")}
    assert created <= locked, f"not locked down: {sorted(created - locked)}"
    lower = sql.lower()
    for needed in ("enable row level security", "force row level security",
                   "create policy commissioner_all on %i for all to "
                   "authenticated", "revoke all on %i from anon",
                   "raise exception 'rls is not enabled and forced"):
        assert needed in lower, f"0007 is missing: {needed}"


def test_zero_seven_grants_anon_nothing_and_says_so_out_loud():
    """`revoke` is the instruction; the assert block is the proof. 0006
    learned this the hard way with the PUBLIC grant on a function."""
    lower = ZERO_SEVEN.read_text(encoding="utf-8").lower()
    assert "grantee = 'anon'" in lower
    assert "raise exception 'anon still holds a grant on site_documents'" in lower
    assert "to anon" not in lower.replace("from anon", ""), \
        "nothing in 0007 may grant anon anything"
