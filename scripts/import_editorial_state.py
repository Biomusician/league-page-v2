"""Move the Commissioner's authored state into the cloud, once, on purpose.

    # what WOULD be written, and nothing else (the default)
    .venv/Scripts/python.exe scripts/import_editorial_state.py

    # actually write it, in one transaction
    .venv/Scripts/python.exe scripts/import_editorial_state.py --apply

The dry run is the gate. It prints, per table, how many rows would be
inserted, how many already match, and how many EXIST IN THE CLOUD WITH
DIFFERENT CONTENT -- and that last number is the one that matters,
because a row that differs is a row somebody edited on the other side.
The import refuses to run while any exist unless it is told to overwrite
them, so "I did not know that was there" cannot silently become "it is
gone now".

WHAT IS NOT IMPORTED, AND WHY

  Sleeper cache          a resync rebuilds it, all 12,700 rows
  sync_snapshots         the inbox baseline describes a queue that a
                         resync replaces; carrying `reviewed_at` forward
                         would mark unseen changes as seen
  meta / editorial_meta  recomputable settings, and the two stores do not
                         even agree on the table's name
  prose                  already in the cloud and verified identical.
                         This script checks that and refuses if it is not
                         true any more, rather than writing words.

WHAT IS DELIBERATELY COPIED VERBATIM

`approved_sha` is copied exactly as it stands, including where it is
NULL. An approval with no signature is a historical fact -- somebody
clicked before signatures existed -- and computing one during a migration
would manufacture a claim that the Commissioner never made about text he
may never have read. `module_approved` already reads a null signature as
legacy rather than as current approval. That is the correct outcome and
this script must not improve on it.

`baseline_text` IS copied. It is the generated draft a section started
from, it is how the Desk says how far he has moved from it, and it is
authenticated editorial state. It must never leave that boundary; see
tests/test_baseline_text_stays_private.py, which proves it against built
output rather than trusting this comment.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import prose_store, settings  # noqa: E402
from leaguepage.config import EDITORIAL_DIR, LEAGUES  # noqa: E402
from leaguepage.storage import Storage  # noqa: E402

# table -> the columns that identify a row. Everything else the two sides
# have in common travels with it.
IDENTITY: dict[str, tuple[str, ...]] = {
    "issues": ("league_slug", "season", "issue_key"),
    "issue_modules": ("league_slug", "season", "issue_key", "module_key"),
    "prose_provenance": ("league_slug", "season", "issue_key", "section"),
    "matchup_state": ("league_slug", "season", "week", "matchup_slug"),
    "prose_revisions": ("id",),
    "issue_revision_requests": ("id",),
    "takes": ("take_id",),
    "story_decisions": ("league_slug", "season", "workflow", "candidate_id"),
    "award_decisions": ("league_slug", "season", "workflow", "award_key"),
    "power_rankings": ("league_slug", "season", "label", "roster_id"),
    "team_names": ("league_slug", "roster_id"),
    "force_flow_notes": ("league_slug", "season", "txn_id"),
    "editorial_usage": ("usage_id",),
    "bit_usage": ("usage_id",),
}

# Order matters only for readability: nothing here has a foreign key.
ORDER = list(IDENTITY)

# Tables whose primary key is a serial the import preserves, so a
# revision id in somebody's History panel still means the same revision.
# Their sequences are advanced afterwards.
SERIALS = {"prose_revisions": ("id", "prose_revisions_id_seq"),
           "issue_revision_requests": ("id", "issue_revision_requests_id_seq"),
           "takes": ("take_id", "takes_take_id_seq"),
           "editorial_usage": ("usage_id", "editorial_usage_usage_id_seq"),
           "bit_usage": ("usage_id", "bit_usage_usage_id_seq")}

QUOTED = {"rank"}


def _q(c: str) -> str:
    return f'"{c}"' if c in QUOTED else c


def _local_columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _cloud_columns(pg, table: str) -> list[str]:
    return [r[0] for r in pg.execute(
        "select column_name from information_schema.columns "
        "where table_schema='public' and table_name=%s "
        "order by ordinal_position", (table,)).fetchall()]


def _cloud_types(pg, table: str) -> dict[str, str]:
    """column -> Postgres data_type, for the destination schema."""
    return {r[0]: r[1] for r in pg.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_schema='public' and table_name=%s", (table,)).fetchall()}


# What each destination type accepts from SQLite once normalised. The list
# is deliberately short: only types this import actually carries, and only
# mappings the schema makes unambiguous. Postgres parses the ISO strings
# SQLite keeps its timestamps in, which is why `text` is what a
# `timestamp with time zone` column is fed -- proved rather than assumed
# by the four `issues` rows that went in on the first attempt.
ACCEPTS: dict[str, tuple[str, ...]] = {
    "boolean": ("bool",),
    "integer": ("int",),
    "bigint": ("int",),
    "text": ("str",),
    "timestamp with time zone": ("str",),
}

# Values of these destination types are safe to name in an error message.
# A `text` column may be prose, a baseline draft, or a Commissioner's
# note, and a preflight failure is not a reason to print any of it.
SHOWABLE = {"boolean", "integer", "bigint"}


def _kind(v) -> str:
    """The value's type for matching against ACCEPTS. `bool` before `int`,
    because in Python bool IS an int and the two are not interchangeable
    at a Postgres column boundary."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    return type(v).__name__


def as_bool(v):
    """SQLite's 0/1 as a real boolean, or a refusal.

    SQLite has no boolean type; it stores 0 and 1 in an INTEGER column and
    hands them back as Python ints. Postgres will not take a smallint for
    a BOOLEAN and psycopg does not guess, which is exactly right -- and is
    what stopped the first production import halfway through
    `issue_modules`.

    0 and 1 are the only values with an unambiguous meaning here, so they
    are the only ones accepted. Anything else is a fact about the data
    nobody has looked at, and coercing it by truthiness would turn 2, ""
    or "false" into an answer this script invented.
    """
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    raise ValueError(f"{v!r} is not 0, 1, true, false or NULL")


def normalize(rows: list[tuple], cols: list[str],
              types: dict[str, str]) -> list[tuple]:
    """Rows as the destination schema needs them.

    Only booleans are converted, because boolean is the only column type
    where the two sides disagree about the representation rather than
    about the spelling. Everything else is passed through untouched: a
    migration that reformats values it was not asked to reformat is a
    migration that rewrites data.
    """
    at = [i for i, c in enumerate(cols) if types.get(c) == "boolean"]
    if not at:
        return rows
    out = []
    for r in rows:
        r = list(r)
        for i in at:
            r[i] = as_bool(r[i])
        out.append(tuple(r))
    return out


def preflight(p: dict, types_by_table: dict[str, dict[str, str]]) -> list[str]:
    """Every value that would be written, checked against the column it
    would land in, before the first write.

    The dry run proved the KEYS: what is new, what matches, what somebody
    changed on the other side. It did not prove the VALUES were
    bind-compatible with the destination schema, and that gap is what let
    a datatype mismatch surface as an exception in the middle of an APPLY
    that had already committed a table.
    """
    problems: list[str] = []
    for table in ORDER:
        d = p.get(table)
        if not d:
            continue
        types = types_by_table.get(table, {})
        rows = list(d["insert"]) + [r for r, _ in d["differs"]]
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            for col, value in zip(d["columns"], row):
                dest = types.get(col)
                if dest is None:
                    continue
                try:
                    value = as_bool(value) if dest == "boolean" else value
                except ValueError as exc:
                    key = (col, dest, "bad-boolean")
                    if key not in seen:
                        seen.add(key)
                        problems.append(
                            f"{table}.{col}: {dest} column cannot take "
                            f"{exc}")
                    continue
                kind = _kind(value)
                if kind == "null":
                    continue
                allowed = ACCEPTS.get(dest)
                if allowed is None:
                    key = (col, dest, "unknown-type")
                    if key not in seen:
                        seen.add(key)
                        problems.append(
                            f"{table}.{col}: destination type {dest!r} is "
                            f"not one this import knows how to check")
                elif kind not in allowed:
                    key = (col, dest, kind)
                    if key not in seen:
                        seen.add(key)
                        shown = (f" (value {value!r})"
                                 if dest in SHOWABLE else "")
                        problems.append(
                            f"{table}.{col}: {dest} column would receive a "
                            f"python {kind}{shown}")
    return problems


def plan(s: Storage, pg) -> tuple[dict, list[str]]:
    """What each table would gain, keep, or overwrite. Reads only."""
    out: dict[str, dict] = {}
    notes: list[str] = []
    for table in ORDER:
        ident = IDENTITY[table]
        local_cols = _local_columns(s._conn, table)             # noqa: SLF001
        cloud_cols = _cloud_columns(pg, table)
        if not local_cols or not cloud_cols:
            notes.append(f"{table}: absent on "
                         f"{'local' if not local_cols else 'cloud'}")
            continue
        carried = [c for c in local_cols if c in cloud_cols]
        missing_here = [c for c in cloud_cols if c not in local_cols]
        dropped = [c for c in local_cols if c not in cloud_cols]
        rows = [tuple(r) for r in s._conn.execute(                # noqa: SLF001
            f"SELECT {', '.join(_q(c) for c in carried)} FROM {table}"
        ).fetchall()]
        with pg.transaction(force_rollback=True):
            cur = pg.execute(
                f"SELECT {', '.join(_q(c) for c in carried)} FROM {table}")
            cloud = [tuple(r) for r in cur.fetchall()]
        idx = [carried.index(c) for c in ident]

        def key(row, idx=idx):
            return tuple(row[i] for i in idx)

        cmap = {key(r): r for r in cloud}
        insert, same, differs = [], [], []
        for r in rows:
            other = cmap.get(key(r))
            if other is None:
                insert.append(r)
            elif _comparable(other) == _comparable(r):
                same.append(r)
            else:
                differs.append((r, other))
        out[table] = {"columns": carried, "identity": ident,
                      "insert": insert, "same": same, "differs": differs,
                      "cloud_total": len(cloud), "local_total": len(rows),
                      "cloud_only": len(cmap) - len(same) - len(differs),
                      "dropped": dropped, "absent_locally": missing_here}
    return out, notes


# Only an actual ISO timestamp is normalised. Matching on "long enough
# and a separator at index 10" also matched a note whose eleventh
# character happened to be a space, which turned an identical row into a
# reported difference and blocked the import with a confusing message.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _comparable(row: tuple) -> tuple:
    """Compare on values, not on how each driver spells them.

    Postgres hands back datetimes and booleans where SQLite hands back
    strings and integers, and a migration that called those different
    would rewrite every row it had already written.
    """
    out = []
    for v in row:
        if isinstance(v, bool):
            out.append(1 if v else 0)
        elif hasattr(v, "isoformat"):
            out.append(v.isoformat()[:19].replace(" ", "T"))
        elif isinstance(v, str) and _TIMESTAMP.match(v):
            out.append(v[:19].replace(" ", "T"))
        else:
            out.append(v)
    return tuple(out)


def prose_matches(pg) -> tuple[int, int, list[str]]:
    """Prose is already in the cloud. Confirm that before writing anything.

    Not an import step: tranche 4 moved the words and verified them. This
    is here because a metadata import into a database whose prose has
    drifted would describe text that is not there.
    """
    repo = prose_store.FilesystemProseRepository(base_dir=EDITORIAL_DIR)
    local = {}
    for season_dir in sorted(EDITORIAL_DIR.glob("[0-9][0-9][0-9][0-9]")):
        for league in LEAGUES:
            parent = season_dir / league.slug
            if not parent.is_dir():
                continue
            for idir in sorted(d for d in parent.iterdir() if d.is_dir()):
                for rec in repo.list_issue(league.slug, season_dir.name,
                                           idir.name):
                    if rec.exists:
                        k = rec.key
                        local[(k.league, k.season, k.issue, k.kind, k.name)] = rec.text
    with pg.transaction(force_rollback=True):
        cur = pg.execute("select league_slug, season, issue_key, kind, "
                         "section, content from sections")
        cloud = {tuple(r[:5]): r[5] for r in cur.fetchall()}
    bad = []
    for k, text in local.items():
        if k not in cloud:
            bad.append(f"local only: {'/'.join(str(x) for x in k)}")
        elif cloud[k] != text:
            bad.append(f"differs: {'/'.join(str(x) for x in k)}")
    for k in cloud:
        if k not in local:
            bad.append(f"cloud only: {'/'.join(str(x) for x in k)}")
    return len(local), len(cloud), bad


def state_plan(s: Storage, pg) -> tuple[list[tuple], int, list[str]]:
    """Which sections should be marked commissioner-edited in the cloud.

    Returns the rows to set, how many are already right, and anything
    that makes the reconciliation unsafe to run.
    """
    rows = s._conn.execute(                                     # noqa: SLF001
        "SELECT league_slug, season, issue_key, section, state "
        "FROM section_prose_state").fetchall()
    local = {}
    for r in rows:
        try:
            k = prose_store.ProseKey.for_section(r[0], r[1], r[2], r[3])
        except prose_store.ProseError:
            continue
        local[(k.league, k.season, k.issue, k.kind, k.name)] = r[4]
    with pg.transaction(force_rollback=True):
        cur = pg.execute("select league_slug, season, issue_key, kind, "
                         "section, state from sections")
        cloud = {tuple(r[:5]): r[5] for r in cur.fetchall()}

    problems = []
    claimed = sorted(k for k, v in cloud.items()
                     if v != "generated" and local.get(k) != v)
    if claimed:
        problems.append(
            f"{len(claimed)} cloud section(s) already carry a state this "
            "machine does not agree with; somebody set them there")
    to_set, already = [], 0
    for k, state in local.items():
        if k not in cloud:
            problems.append(f"no cloud section for {'/'.join(map(str, k))}")
        elif cloud[k] == state:
            already += 1
        else:
            to_set.append((state, *k))
    return to_set, already, problems


def show(p: dict, notes: list[str], prose: tuple, states: tuple) -> int:
    n_local, n_cloud, bad = prose
    print(f"{'table':26s} {'local':>7s} {'cloud':>7s} {'insert':>7s} "
          f"{'same':>7s} {'DIFFERS':>8s} {'cloud-only':>11s}")
    total_insert = total_differs = 0
    for table in ORDER:
        d = p.get(table)
        if d is None:
            continue
        total_insert += len(d["insert"])
        total_differs += len(d["differs"])
        print(f"{table:26s} {d['local_total']:7d} {d['cloud_total']:7d} "
              f"{len(d['insert']):7d} {len(d['same']):7d} "
              f"{len(d['differs']):8d} {d['cloud_only']:11d}")
    print(f"\n{'TOTAL':26s} {'':7s} {'':7s} {total_insert:7d} {'':7s} "
          f"{total_differs:8d}")
    print(f"\nprose: {n_local} local, {n_cloud} cloud, "
          f"{'identical' if not bad else str(len(bad)) + ' MISMATCHED'}")
    for line in bad[:10]:
        print(f"  {line}")
    to_set, already, sproblems = states
    print(f"section state: {len(to_set)} to mark commissioner-edited, "
          f"{already} already right")
    print("  (a section with no local row stays generated: the absence of "
          "a row is not a record of anything)")
    for line in sproblems:
        print(f"  PROBLEM: {line}")
    for table in ORDER:
        d = p.get(table)
        if d and (d["dropped"] or d["absent_locally"]):
            print(f"\n{table}:")
            if d["dropped"]:
                print(f"  local columns with no cloud home (NOT carried): "
                      f"{', '.join(d['dropped'])}")
            if d["absent_locally"]:
                print(f"  cloud columns nothing local fills: "
                      f"{', '.join(d['absent_locally'])}")
    for line in notes:
        print(f"note: {line}")
    return total_insert, total_differs, bad


def apply(pg, p: dict, actor: str, overwrite: bool,
          to_set: list[tuple] | None = None,
          types_by_table: dict[str, dict[str, str]] | None = None) -> dict:
    """One transaction. Every table or none of them.

    That sentence used to be a comment rather than a fact. The connection
    is opened with `autocommit=True` so the read-only planning above can
    run, and under autocommit a bare `with pg.cursor()` is not a
    transaction: every statement committed on its own, so the first
    production attempt left a committed `issues` table behind when
    `issue_modules` raised. Worse, `SET LOCAL` and `set_config(..., true)`
    are scoped to a transaction that did not exist, so both were discarded
    immediately and the whole import ran as the connection's own role --
    which carries BYPASSRLS. The authorization boundary this script's
    docstring claims was not merely weakened; it was absent.

    `pg.transaction()` is what makes both claims true at once. It opens an
    explicit block even on an autocommit connection, so the settings live
    for exactly the write phase, and an exception anywhere inside rolls
    back every table.
    """
    written = {}
    types_by_table = types_by_table or {}
    with pg.transaction(), pg.cursor() as cur:
        cur.execute("select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"role": "authenticated", "email": actor}),))
        cur.execute("set local role authenticated")
        # Proof rather than intention: if the settings did not take, the
        # rows would land as the owner and nothing downstream would say so.
        role = cur.execute("select current_role").fetchone()[0]
        if role != "authenticated":
            raise RuntimeError(
                f"refusing to write as {role!r}: the import runs as the "
                "signed-in Commissioner so RLS applies to it exactly as it "
                "does to the Desk")
        for table in ORDER:
            d = p.get(table)
            if not d:
                continue
            rows = list(d["insert"]) + ([r for r, _ in d["differs"]]
                                        if overwrite else [])
            if not rows:
                continue
            cols = d["columns"]
            rows = normalize(rows, cols, types_by_table.get(table, {}))
            names = ", ".join(_q(c) for c in cols)
            marks = ", ".join(["%s"] * len(cols))
            sets = ", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in cols
                             if c not in d["identity"])
            conflict = ", ".join(_q(c) for c in d["identity"])
            sql = (f"insert into {table} ({names}) values ({marks}) "
                   f"on conflict ({conflict}) do update set {sets}"
                   if sets else
                   f"insert into {table} ({names}) values ({marks}) "
                   f"on conflict ({conflict}) do nothing")
            cur.executemany(sql, rows)
            written[table] = len(rows)
        if to_set:
            cur.executemany(
                "update sections set state=%s where league_slug=%s and "
                "season=%s and issue_key=%s and kind=%s and section=%s",
                to_set)
            written["sections.state"] = len(to_set)
        for table, (col, seq) in SERIALS.items():
            if table in written:
                # The ids came across verbatim so History links still
                # resolve; the sequence has to be told, or the next insert
                # collides with a row this import just wrote.
                cur.execute(
                    f"select setval(%s, coalesce((select max({_q(col)}) "
                    f"from {table}), 1))", (seq,))
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write. Without this, nothing is written.")
    ap.add_argument("--overwrite", action="store_true",
                    help="also replace cloud rows whose content differs. "
                         "Refused by default: a row that differs is a row "
                         "somebody edited on the other side.")
    ap.add_argument("--actor", default="",
                    help="the Commissioner to act as. Defaults to the "
                         "first address in the live allowlist.")
    args = ap.parse_args()

    dsn = settings.get(settings.DATABASE_URL)
    if not dsn:
        print("DATABASE_URL is not configured.")
        return 1
    import psycopg

    with Storage() as s, psycopg.connect(dsn, connect_timeout=30,
                                         autocommit=True) as pg:
        p, notes = plan(s, pg)
        prose = prose_matches(pg)
        states = state_plan(s, pg)
        inserts, differs, bad = show(p, notes, prose, states)

        # Every value that would be written, against the column it would
        # land in, on the dry run as well as the real one. The dry run
        # proved the keys and said nothing about the types, which is how
        # a BOOLEAN column taking a SQLite 0 became a live exception
        # halfway through a write instead of a refusal before one.
        types_by_table = {t: _cloud_types(pg, t) for t in ORDER}
        type_problems = preflight(p, types_by_table)
        if type_problems:
            print(f"\nTYPE PREFLIGHT: {len(type_problems)} problem(s)")
            for line in type_problems:
                print(f"  {line}")
        else:
            print("\ntype preflight: every value fits its destination column")

        if not args.apply:
            print("\nDRY RUN. Nothing was written. Re-run with --apply to "
                  "write exactly the INSERT column above.")
            return 0
        if type_problems:
            print("\nREFUSING BEFORE WRITE: a value does not fit the column "
                  "it would be written to. Nothing has been written.")
            return 1
        if bad:
            print("\nREFUSING: the cloud's prose is not the prose on this "
                  "machine. Metadata describes text; importing it against "
                  "different text would describe the wrong words.")
            return 1
        if differs and not args.overwrite:
            print(f"\nREFUSING: {differs} row(s) exist in the cloud with "
                  "different content. Look at them, then re-run with "
                  "--overwrite if replacing them is what you mean.")
            return 1
        actor = args.actor
        if not actor:
            row = pg.execute("select email from app_commissioners "
                             "order by email limit 1").fetchone()
            actor = row[0] if row else ""
        if not actor:
            print("\nREFUSING: no Commissioner to act as. The import runs "
                  "as the signed-in user so RLS applies to it, exactly as "
                  "it would to the Desk.")
            return 1
        if states[2]:
            print("\nREFUSING: the section-state reconciliation is not safe "
                  "to run. Read the PROBLEM lines above.")
            return 1
        written = apply(pg, p, actor, args.overwrite, states[0],
                        types_by_table)
        print(f"\nWROTE {sum(written.values())} row(s) in one transaction:")
        for table, n in written.items():
            print(f"  {table:26s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
