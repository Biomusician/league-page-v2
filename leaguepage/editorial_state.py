"""The editorial metadata that describes prose, behind one narrow port.

WHY THIS EXISTS

Tranche 5A proved the contract locally: one Commissioner action commits
or does not commit, and every claim about prose carries the identity of
the prose it describes. Locally the second half does the heavy lifting,
because SQLite cannot commit a filesystem write and a save is therefore
two transaction owners -- the repository for the prose, the route for
everything describing it.

In Postgres both halves can be one transaction. That only helps if the
metadata writes can be made to run on the SAME connection as the prose
write, which means they cannot go through `Storage` (SQLite) directly.
So the six things a prose action writes get a port with two
implementations, and `EditorialStore` binds one of them to the
transaction it owns.

WHAT IS DELIBERATELY NOT HERE

Everything that is not part of a prose action. Takes, story decisions,
award decisions, rankings and the rest are single-table writes with their
own routes; widening this port to cover them would turn it into a second
Storage. They move in their own step.
"""
from __future__ import annotations

import json
from typing import Protocol

from leaguepage.prose_store import ProseKey


class EditorialState(Protocol):
    """Reads and writes the metadata one prose action touches.

    Every method is scoped to a single issue or a single section, because
    that is the scope of a Commissioner click. Nothing here returns SQL or
    a cursor: the point is that a route cannot tell which store it is
    talking to.
    """

    # -- prose state ---------------------------------------------------

    def prose_state(self, key: ProseKey) -> str | None: ...

    def set_prose_state(self, key: ProseKey, state: str) -> None: ...

    # -- provenance ----------------------------------------------------

    def provenance(self, key: ProseKey) -> dict | None: ...

    def set_provenance(self, key: ProseKey, row: dict) -> None: ...

    def set_assistance(self, key: ProseKey, assistance: str) -> None: ...

    # -- modules and matchups ------------------------------------------

    def module(self, league: str, season: str, issue: str,
               module_key: str) -> dict | None: ...

    def set_module(self, league: str, season: str, issue: str,
                   module_key: str, **fields) -> None: ...

    def matchup(self, league: str, season: str, week: int,
                slug: str) -> dict | None: ...

    def set_matchup(self, league: str, season: str, week: int,
                    slug: str, **fields) -> None: ...

    # -- the rewrite queue ---------------------------------------------

    def resolve_rewrite_requests(self, league: str, season: str, issue: str,
                                 section: str, status: str) -> None: ...


MODULE_FIELDS = ("position", "included", "custom_title", "approved",
                 "approved_sha")
MATCHUP_FIELDS = ("selected_angle_id", "custom_angle", "angle_note",
                  "prominence_override", "status", "revision_requests",
                  "covered_sha")
PROVENANCE_FIELDS = ("generator", "method", "generated_sha", "origin",
                     "assistance", "baseline_text", "event")


class SqliteEditorialState:
    """The port over `Storage`, which is what runs today.

    A thin adapter on purpose. It adds no behaviour: the semantics live in
    Storage and in the callers, and this exists so the same route code can
    run against either store.
    """

    backend = "sqlite"

    def __init__(self, storage) -> None:
        self._s = storage

    # -- prose state ---------------------------------------------------

    def prose_state(self, key: ProseKey) -> str | None:
        return self._s.get_prose_states(key.league, key.season,
                                        key.issue).get(key.section_id)

    def set_prose_state(self, key: ProseKey, state: str) -> None:
        self._s.set_prose_state(key.league, key.season, key.issue,
                                key.section_id, state)

    # -- provenance ----------------------------------------------------

    def provenance(self, key: ProseKey) -> dict | None:
        return self._s.get_prose_provenance(key.league, key.season, key.issue,
                                            key.section_id)

    def set_provenance(self, key: ProseKey, row: dict) -> None:
        # A full row, not a patch. `provenance.record` builds a complete
        # claim and the store replaces the previous one, so a field the
        # caller left out means "no longer true" rather than "unchanged".
        self._s.set_prose_provenance(
            league_slug=key.league, season=key.season, issue_key=key.issue,
            section=key.section_id,
            generator=row.get("generator"), method=row.get("method"),
            generated_sha=row.get("generated_sha") or "",
            origin=row.get("origin"), assistance=row.get("assistance"),
            baseline_text=row.get("baseline_text"), event=row.get("event"))

    def set_assistance(self, key: ProseKey, assistance: str) -> None:
        self._s.set_prose_assistance(
            league_slug=key.league, season=key.season, issue_key=key.issue,
            section=key.section_id, assistance=assistance)

    # -- modules and matchups ------------------------------------------

    def module(self, league: str, season: str, issue: str,
               module_key: str) -> dict | None:
        return self._s.get_issue_modules(league, season, issue).get(module_key)

    def set_module(self, league: str, season: str, issue: str,
                   module_key: str, **fields) -> None:
        self._s.set_issue_module(league_slug=league, season=season,
                                 issue_key=issue, module_key=module_key,
                                 **fields)

    def matchup(self, league: str, season: str, week: int,
                slug: str) -> dict | None:
        return self._s.get_matchup_state(league_slug=league, season=season,
                                         week=week, matchup_slug=slug)

    def set_matchup(self, league: str, season: str, week: int,
                    slug: str, **fields) -> None:
        self._s.set_matchup_state(league_slug=league, season=season, week=week,
                                  matchup_slug=slug, **fields)

    # -- the rewrite queue ---------------------------------------------

    def resolve_rewrite_requests(self, league: str, season: str, issue: str,
                                 section: str, status: str) -> None:
        self._s.resolve_rewrite_requests(league, season, issue, section, status)


class PostgresEditorialState:
    """The same port on a cursor somebody else owns.

    Every statement here runs inside the caller's transaction, which is
    the whole point: the prose write and the claims about it commit
    together or not at all.
    """

    backend = "postgres"

    def __init__(self, cursor) -> None:
        self._cur = cursor

    def _one(self, sql: str, args: tuple) -> dict | None:
        self._cur.execute(sql, args)
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return dict(zip(cols, row))

    # -- prose state ---------------------------------------------------
    #
    # Postgres keeps this on `sections.state` rather than in a table of its
    # own. That was the whole of the "missing section_prose_state" finding:
    # the column existed and nothing wrote it, because the Desk called
    # Storage while the repository wrote only content and version.

    def prose_state(self, key: ProseKey) -> str | None:
        row = self._one(
            "select state from sections where league_slug=%s and season=%s "
            "and issue_key=%s and kind=%s and section=%s",
            (key.league, key.season, key.issue, key.kind, key.name))
        return row["state"] if row else None

    def set_prose_state(self, key: ProseKey, state: str) -> None:
        if state not in ("generated", "commissioner-edited"):
            raise ValueError(f"unknown prose state: {state}")
        self._cur.execute(
            "update sections set state=%s where league_slug=%s and season=%s "
            "and issue_key=%s and kind=%s and section=%s",
            (state, key.league, key.season, key.issue, key.kind, key.name))

    # -- provenance ----------------------------------------------------

    def provenance(self, key: ProseKey) -> dict | None:
        return self._one(
            "select * from prose_provenance where league_slug=%s and season=%s "
            "and issue_key=%s and section=%s",
            (key.league, key.season, key.issue, key.section_id))

    def set_provenance(self, key: ProseKey, row: dict) -> None:
        # Every field, every time, exactly as the SQLite side does: a
        # claim is replaced wholesale, so a field the caller omitted means
        # "no longer true" and not "leave the old value there".
        values = [row.get(f) for f in PROVENANCE_FIELDS]
        values[PROVENANCE_FIELDS.index("generated_sha")] = (
            row.get("generated_sha") or "")
        cols = ", ".join(PROVENANCE_FIELDS)
        marks = ", ".join(["%s"] * len(PROVENANCE_FIELDS))
        sets = ", ".join(f"{f}=excluded.{f}" for f in PROVENANCE_FIELDS)
        self._cur.execute(
            f"insert into prose_provenance (league_slug, season, issue_key, "
            f"section, recorded_at, {cols}) "
            f"values (%s,%s,%s,%s, now(), {marks}) "
            f"on conflict (league_slug, season, issue_key, section) do update "
            f"set recorded_at = now(), {sets}",
            (key.league, key.season, key.issue, key.section_id, *values))

    def set_assistance(self, key: ProseKey, assistance: str) -> None:
        # Assistance is the one provenance field that is added WITHOUT
        # touching origin: AI help reached the section, and who wrote it is
        # a separate question with a separate answer.
        self._cur.execute(
            "insert into prose_provenance (league_slug, season, issue_key, "
            "section, assistance, origin) values (%s,%s,%s,%s,%s,'unknown') "
            "on conflict (league_slug, season, issue_key, section) do update "
            "set assistance = excluded.assistance",
            (key.league, key.season, key.issue, key.section_id, assistance))

    # -- modules and matchups ------------------------------------------

    def module(self, league: str, season: str, issue: str,
               module_key: str) -> dict | None:
        return self._one(
            "select * from issue_modules where league_slug=%s and season=%s "
            "and issue_key=%s and module_key=%s",
            (league, season, issue, module_key))

    def set_module(self, league: str, season: str, issue: str,
                   module_key: str, **fields) -> None:
        bad = set(fields) - set(MODULE_FIELDS)
        if bad:
            raise ValueError(f"Unknown issue_modules fields: {bad}")
        if "approved" in fields:
            fields["approved"] = bool(fields["approved"])
        if "included" in fields:
            fields["included"] = bool(fields["included"])
        names = list(fields)
        cols = ", ".join(names)
        marks = ", ".join(["%s"] * len(names))
        sets = ", ".join(f"{f}=excluded.{f}" for f in names)
        self._cur.execute(
            f"insert into issue_modules (league_slug, season, issue_key, "
            f"module_key, updated_at{', ' + cols if cols else ''}) "
            f"values (%s,%s,%s,%s, now(){', ' + marks if marks else ''}) "
            f"on conflict (league_slug, season, issue_key, module_key) "
            f"do update set updated_at = now()"
            f"{', ' + sets if sets else ''}",
            (league, season, issue, module_key, *[fields[f] for f in names]))

    def matchup(self, league: str, season: str, week: int,
                slug: str) -> dict | None:
        return self._one(
            "select * from matchup_state where league_slug=%s and season=%s "
            "and week=%s and matchup_slug=%s", (league, season, week, slug))

    def set_matchup(self, league: str, season: str, week: int,
                    slug: str, **fields) -> None:
        bad = set(fields) - set(MATCHUP_FIELDS)
        if bad:
            raise ValueError(f"Unknown matchup_state fields: {bad}")
        if "revision_requests" in fields and not isinstance(
                fields["revision_requests"], (str, type(None))):
            fields["revision_requests"] = json.dumps(fields["revision_requests"])
        names = list(fields)
        cols = ", ".join(names)
        marks = ", ".join(["%s"] * len(names))
        sets = ", ".join(f"{f}=excluded.{f}" for f in names)
        self._cur.execute(
            f"insert into matchup_state (league_slug, season, week, "
            f"matchup_slug, updated_at{', ' + cols if cols else ''}) "
            f"values (%s,%s,%s,%s, now(){', ' + marks if marks else ''}) "
            f"on conflict (league_slug, season, week, matchup_slug) "
            f"do update set updated_at = now()"
            f"{', ' + sets if sets else ''}",
            (league, season, week, slug, *[fields[f] for f in names]))

    # -- the rewrite queue ---------------------------------------------

    def resolve_rewrite_requests(self, league: str, season: str, issue: str,
                                 section: str, status: str) -> None:
        self._cur.execute(
            "update issue_revision_requests set status=%s, resolved_at=now() "
            "where league_slug=%s and season=%s and issue_key=%s "
            "and section=%s and status='open'",
            (status, league, season, issue, section))
