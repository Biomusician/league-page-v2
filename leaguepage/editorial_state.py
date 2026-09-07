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

    def modules(self, league: str, season: str,
                issue: str) -> dict[str, dict]: ...

    def set_module(self, league: str, season: str, issue: str,
                   module_key: str, **fields) -> None: ...

    def matchup(self, league: str, season: str, week: int,
                slug: str) -> dict | None: ...

    def set_matchup(self, league: str, season: str, week: int,
                    slug: str, **fields) -> None: ...

    def log_usage(self, league: str, season: str, week: int | None, kind: str,
                  value: str, *, matchup_slug: str | None = None,
                  note: str | None = None) -> None: ...

    # -- the issue row -------------------------------------------------

    def issue(self, league: str, season: str, issue_key: str) -> dict | None: ...

    def set_issue(self, league: str, season: str, issue_key: str,
                  **fields) -> None: ...

    # -- rankings ------------------------------------------------------
    #
    # Saved whole. The Commissioner ranks a league, not a team, and a
    # partial save is a table with two number sevens in it.

    def rankings(self, league: str, season: str, label: str) -> list[dict]: ...

    def set_rankings(self, league: str, season: str, label: str,
                     entries: list[dict]) -> None: ...

    # -- what he decided about a candidate or an award ------------------
    #
    # Keyword-only, like the Storage methods they mirror, and for the
    # same reason: the arguments are five strings whose order nobody
    # remembers, and a wrong one is a decision recorded against the wrong
    # candidate rather than an error.

    def set_story_decision(self, *, league: str, season: str, workflow: str,
                           candidate_id: str, decision: str,
                           note: str | None = None,
                           route: str | None = None) -> None: ...

    def set_award_decision(self, *, league: str, season: str, workflow: str,
                           award_key: str, decision: str,
                           winner: str | None = None,
                           note: str | None = None) -> None: ...

    # -- research ------------------------------------------------------
    #
    # What a Claude Code session left for him to read. Not prose: nothing
    # here publishes, and losing it costs a regeneration rather than
    # writing. It is here because the application READS it on a path
    # that decides what gets written -- what Reset puts back, and whether
    # a save records that AI help was present.

    def research(self, league: str, season: str, issue: str, scope: str,
                 name: str) -> str | None: ...

    def set_research(self, league: str, season: str, issue: str, scope: str,
                     name: str, body: str) -> None: ...

    # -- takes ---------------------------------------------------------
    #
    # A take has an identity of its own, so these are addressed by id
    # rather than by league and season. `add_take` returns it.

    def add_take(self, **fields) -> int: ...

    def take(self, take_id: int) -> dict | None: ...

    def set_take_status(self, take_id: int, status: str,
                        resolution: str | None = None) -> None: ...

    def set_take_public(self, take_id: int, public: bool) -> None: ...

    def delete_take(self, take_id: int) -> None: ...

    # -- public team names ---------------------------------------------

    def set_team_name(self, league: str, roster_id: int, name: str) -> None: ...

    def clear_team_name(self, league: str, roster_id: int) -> None: ...

    # -- the rest ------------------------------------------------------

    def set_force_flow_note(self, *, league: str, season: str, txn_id: str,
                            note: str) -> None: ...

    def add_rewrite_request(self, league: str, season: str, issue: str,
                            section: str, note: str) -> int: ...

    def mark_sync_reviewed(self, league: str, season: str) -> str | None: ...

    # -- the rewrite queue ---------------------------------------------

    def resolve_rewrite_requests(self, league: str, season: str, issue: str,
                                 section: str, status: str) -> None: ...


# Only what a route asks for today. An issue's status belongs to
# publication, which is deliberately not hosted, so it is not here.
TAKE_FIELDS = (
    "league_slug", "season", "week", "source", "subject", "quote",
    "confidence", "context", "author", "players", "topic", "issue_key",
    "subject_type", "subject_name", "subject_roster_id", "review_after",
    "review_week", "verbatim", "href", "note")
ISSUE_FIELDS = ("theme",)
RANKING_FIELDS = ("roster_id", "rank", "tier", "note")
STORY_DECISIONS = ("include", "ignore", "save")
AWARD_DECISIONS = ("awarded", "rejected", "manual")
MODULE_FIELDS = ("position", "included", "custom_title", "approved",
                 "approved_sha")
MATCHUP_FIELDS = ("selected_angle_id", "custom_angle", "angle_note",
                  "prominence_override", "status", "revision_requests",
                  "covered_sha")
PROVENANCE_FIELDS = ("generator", "method", "generated_sha", "origin",
                     "assistance", "baseline_text", "event")


class _LeagueLike:
    """`issue_dir` wants a League and uses exactly one field of it.

    Rather than import the registry to look up a slug the caller already
    has, and fail on a league the registry does not know, this passes the
    slug through. The directory layout is <base>/<season>/<slug>/<issue>
    and nothing else about a League appears in it.
    """

    __slots__ = ("slug",)

    def __init__(self, slug: str) -> None:
        self.slug = slug


class SqliteEditorialState:
    """The port over `Storage`, which is what runs today.

    A thin adapter on purpose. It adds no behaviour: the semantics live in
    Storage and in the callers, and this exists so the same route code can
    run against either store.

    `base_dir` is only for research, which lives in files on this side and
    in a table on the other. Everything else here is SQLite.
    """

    backend = "sqlite"

    def __init__(self, storage, *, base_dir=None) -> None:
        self._s = storage
        self._base_dir = base_dir

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

    def modules(self, league: str, season: str, issue: str) -> dict[str, dict]:
        return self._s.get_issue_modules(league, season, issue)

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

    def log_usage(self, league: str, season: str, week: int | None, kind: str,
                  value: str, *, matchup_slug: str | None = None,
                  note: str | None = None) -> None:
        self._s.log_editorial_usage(
            league_slug=league, season=season, week=week, kind=kind,
            value=value, matchup_slug=matchup_slug, note=note)

    # -- the issue row -------------------------------------------------

    def issue(self, league: str, season: str, issue_key: str) -> dict | None:
        return self._s.get_issue(league, season, issue_key)

    def set_issue(self, league: str, season: str, issue_key: str,
                  **fields) -> None:
        bad = set(fields) - set(ISSUE_FIELDS)
        if bad:
            raise ValueError(f"Unknown issues fields: {bad}")
        if "theme" in fields:
            self._s.set_issue_theme(league, season, issue_key, fields["theme"])

    # -- rankings ------------------------------------------------------

    def rankings(self, league: str, season: str, label: str) -> list[dict]:
        return self._s.get_power_rankings(league, season, label)

    def set_rankings(self, league: str, season: str, label: str,
                     entries: list[dict]) -> None:
        self._s.save_power_rankings(league, season, label, entries)

    # -- what he decided about a candidate or an award ------------------

    def set_story_decision(self, *, league: str, season: str, workflow: str,
                           candidate_id: str, decision: str,
                           note: str | None = None,
                           route: str | None = None) -> None:
        self._s.set_story_decision(
            league_slug=league, season=season, workflow=workflow,
            candidate_id=candidate_id, decision=decision, note=note, route=route)

    def set_award_decision(self, *, league: str, season: str, workflow: str,
                           award_key: str, decision: str,
                           winner: str | None = None,
                           note: str | None = None) -> None:
        self._s.set_award_decision(
            league_slug=league, season=season, workflow=workflow,
            award_key=award_key, decision=decision, winner=winner, note=note)

    # -- research ------------------------------------------------------

    def _research_path(self, league: str, season: str, issue: str,
                       scope: str, name: str):
        from leaguepage.issue_builder import issue_dir

        # `scope` and `name` become path segments here and columns on the
        # other adapter, so they are checked here rather than trusted.
        for part in (scope, name):
            if "/" in part or "\\" in part or part.startswith("."):
                raise ValueError(f"not an artifact path segment: {part!r}")
        return issue_dir(_LeagueLike(league), season, issue,
                         self._base_dir) / scope / name

    def research(self, league: str, season: str, issue: str, scope: str,
                 name: str) -> str | None:
        path = self._research_path(league, season, issue, scope, name)
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, OSError):
            return None

    def set_research(self, league: str, season: str, issue: str, scope: str,
                     name: str, body: str) -> None:
        path = self._research_path(league, season, issue, scope, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    # -- takes ---------------------------------------------------------

    def add_take(self, **fields) -> int:
        bad = set(fields) - set(TAKE_FIELDS)
        if bad:
            raise ValueError(f"Unknown takes fields: {bad}")
        return self._s.add_take(**fields)

    def take(self, take_id: int) -> dict | None:
        return self._s.get_take(take_id)

    def set_take_status(self, take_id: int, status: str,
                        resolution: str | None = None) -> None:
        self._s.set_take_status(take_id, status, resolution)

    def set_take_public(self, take_id: int, public: bool) -> None:
        self._s.set_take_public(take_id, public)

    def delete_take(self, take_id: int) -> None:
        self._s.delete_take(take_id)

    # -- public team names ---------------------------------------------

    def set_team_name(self, league: str, roster_id: int, name: str) -> None:
        self._s.set_public_team_name(league, roster_id, name)

    def clear_team_name(self, league: str, roster_id: int) -> None:
        self._s.delete_public_team_name(league, roster_id)

    # -- the rest ------------------------------------------------------

    def set_force_flow_note(self, *, league: str, season: str, txn_id: str,
                            note: str) -> None:
        self._s.set_force_flow_note(league_slug=league, season=season,
                                    txn_id=txn_id, note=note)

    def add_rewrite_request(self, league: str, season: str, issue: str,
                            section: str, note: str) -> int:
        return self._s.add_rewrite_request(league, season, issue, section, note)

    def mark_sync_reviewed(self, league: str, season: str) -> str | None:
        return self._s.mark_sync_reviewed(league, season)

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

    def modules(self, league: str, season: str, issue: str) -> dict[str, dict]:
        self._cur.execute(
            "select * from issue_modules where league_slug=%s and season=%s "
            "and issue_key=%s", (league, season, issue))
        cols = [d[0] for d in self._cur.description]
        return {r[cols.index("module_key")]: dict(zip(cols, r))
                for r in self._cur.fetchall()}

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

    def log_usage(self, league: str, season: str, week: int | None, kind: str,
                  value: str, *, matchup_slug: str | None = None,
                  note: str | None = None) -> None:
        # Append-only by design: the log is a record of what has been
        # used, and an entry is never revised because the joke was still
        # told.
        self._cur.execute(
            "insert into editorial_usage (league_slug, season, week, "
            "matchup_slug, kind, value, note, used_at) "
            "values (%s,%s,%s,%s,%s,%s,%s, now())",
            (league, season, week, matchup_slug, kind, value, note))

    # -- the issue row -------------------------------------------------

    def issue(self, league: str, season: str, issue_key: str) -> dict | None:
        return self._one(
            "select * from issues where league_slug=%s and season=%s "
            "and issue_key=%s", (league, season, issue_key))

    def set_issue(self, league: str, season: str, issue_key: str,
                  **fields) -> None:
        bad = set(fields) - set(ISSUE_FIELDS)
        if bad:
            raise ValueError(f"Unknown issues fields: {bad}")
        if not fields:
            return
        names = list(fields)
        cols = ", ".join(names)
        marks = ", ".join(["%s"] * len(names))
        sets = ", ".join(f"{f}=excluded.{f}" for f in names)
        self._cur.execute(
            f"insert into issues (league_slug, season, issue_key, updated_at, "
            f"{cols}) values (%s,%s,%s, now(), {marks}) "
            f"on conflict (league_slug, season, issue_key) do update "
            f"set updated_at = now(), {sets}",
            (league, season, issue_key, *[fields[f] for f in names]))

    # -- rankings ------------------------------------------------------

    def rankings(self, league: str, season: str, label: str) -> list[dict]:
        self._cur.execute(
            'select roster_id, "rank", tier, note from power_rankings '
            "where league_slug=%s and season=%s and label=%s "
            'order by "rank" nulls last, roster_id',
            (league, season, label))
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, r)) for r in self._cur.fetchall()]

    def set_rankings(self, league: str, season: str, label: str,
                     entries: list[dict]) -> None:
        """Replaces the whole label, exactly as SQLite does.

        Delete-then-insert inside the caller's transaction, so a ranking
        that failed halfway does not leave the league half-ranked. That
        is the difference this port exists to make: the same two
        statements on SQLite are two commits.
        """
        bad = {k for e in entries for k in e} - set(RANKING_FIELDS)
        if bad:
            raise ValueError(f"Unknown power_rankings fields: {bad}")
        self._cur.execute(
            "delete from power_rankings where league_slug=%s and season=%s "
            "and label=%s", (league, season, label))
        for e in entries:
            self._cur.execute(
                'insert into power_rankings (league_slug, season, label, '
                'roster_id, "rank", tier, note, updated_at) '
                "values (%s,%s,%s,%s,%s,%s,%s, now())",
                (league, season, label, e["roster_id"], e.get("rank"),
                 e.get("tier"), e.get("note")))

    # -- what he decided about a candidate or an award ------------------

    def set_story_decision(self, *, league: str, season: str, workflow: str,
                           candidate_id: str, decision: str,
                           note: str | None = None,
                           route: str | None = None) -> None:
        if decision not in STORY_DECISIONS:
            raise ValueError(f"Invalid story decision {decision!r}")
        # `route` survives a decision that does not mention it, the same
        # COALESCE the SQLite side does: re-deciding a candidate says
        # nothing about where it was routed.
        self._cur.execute(
            "insert into story_decisions (league_slug, season, workflow, "
            "candidate_id, decision, note, route, decided_at) "
            "values (%s,%s,%s,%s,%s,%s,%s, now()) "
            "on conflict (league_slug, season, workflow, candidate_id) "
            "do update set decision=excluded.decision, note=excluded.note, "
            "route=coalesce(excluded.route, story_decisions.route), "
            "decided_at=now()",
            (league, season, workflow, candidate_id, decision, note, route))

    def set_award_decision(self, *, league: str, season: str, workflow: str,
                           award_key: str, decision: str,
                           winner: str | None = None,
                           note: str | None = None) -> None:
        if decision not in AWARD_DECISIONS:
            raise ValueError(f"Invalid award decision {decision!r}")
        self._cur.execute(
            "insert into award_decisions (league_slug, season, workflow, "
            "award_key, decision, winner, note, decided_at) "
            "values (%s,%s,%s,%s,%s,%s,%s, now()) "
            "on conflict (league_slug, season, workflow, award_key) "
            "do update set decision=excluded.decision, "
            "winner=excluded.winner, note=excluded.note, decided_at=now()",
            (league, season, workflow, award_key, decision, winner, note))

    # -- research ------------------------------------------------------

    def research(self, league: str, season: str, issue: str, scope: str,
                 name: str) -> str | None:
        row = self._one(
            "select body from research_artifacts where league_slug=%s and "
            "season=%s and issue_key=%s and scope=%s and name=%s",
            (league, season, issue, scope, name))
        return row["body"] if row else None

    def set_research(self, league: str, season: str, issue: str, scope: str,
                     name: str, body: str) -> None:
        self._cur.execute(
            "insert into research_artifacts (league_slug, season, issue_key, "
            "scope, name, body, updated_at) values (%s,%s,%s,%s,%s,%s, now()) "
            "on conflict (league_slug, season, issue_key, scope, name) "
            "do update set body=excluded.body, updated_at=now()",
            (league, season, issue, scope, name, body))

    # -- takes ---------------------------------------------------------

    def add_take(self, **fields) -> int:
        bad = set(fields) - set(TAKE_FIELDS)
        if bad:
            raise ValueError(f"Unknown takes fields: {bad}")
        fields = {k: v for k, v in fields.items() if v is not None}
        if "players" in fields and not isinstance(fields["players"], str):
            fields["players"] = json.dumps(fields["players"])
        if "verbatim" in fields:
            # SQLite stores this as 0/1 and so does Postgres: 0003 declares
            # it `integer not null default 1` rather than boolean, so the
            # two databases hold the same value and not merely the same
            # meaning.
            fields["verbatim"] = 1 if fields["verbatim"] else 0
        names = list(fields)
        cols = ", ".join(names)
        marks = ", ".join(["%s"] * len(names))
        self._cur.execute(
            f"insert into takes ({cols}, created_at) "
            f"values ({marks}, now()) returning take_id",
            tuple(fields[f] for f in names))
        return self._cur.fetchone()[0]

    def take(self, take_id: int) -> dict | None:
        return self._one("select * from takes where take_id=%s", (take_id,))

    def set_take_status(self, take_id: int, status: str,
                        resolution: str | None = None) -> None:
        from leaguepage.storage import Storage

        # The vocabulary and its aliases belong to the lifecycle, not to
        # either database, so both adapters read them from the same place
        # rather than each keeping a copy that can drift.
        status = Storage._STATUS_ALIASES.get(status, status)
        if status not in Storage.TAKE_STATUSES:
            raise ValueError(f"Invalid take status {status!r}")
        settled = status in ("resolved_right", "resolved_wrong", "void")
        self._cur.execute(
            "update takes set status=%s, resolution=coalesce(%s, resolution), "
            "resolved_at = case when %s then now() else null end "
            "where take_id=%s",
            (status, resolution, settled, take_id))

    def set_take_public(self, take_id: int, public: bool) -> None:
        self._cur.execute("update takes set public=%s where take_id=%s",
                          (1 if public else 0, take_id))

    def delete_take(self, take_id: int) -> None:
        self._cur.execute("delete from takes where take_id=%s", (take_id,))

    # -- public team names ---------------------------------------------

    def set_team_name(self, league: str, roster_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("public_name must be non-empty")
        self._cur.execute(
            "insert into team_names (league_slug, roster_id, public_name, "
            "confirmed_at) values (%s,%s,%s, now()) "
            "on conflict (league_slug, roster_id) do update "
            "set public_name=excluded.public_name, confirmed_at=now()",
            (league, roster_id, name))

    def clear_team_name(self, league: str, roster_id: int) -> None:
        self._cur.execute(
            "delete from team_names where league_slug=%s and roster_id=%s",
            (league, roster_id))

    # -- the rest ------------------------------------------------------

    def set_force_flow_note(self, *, league: str, season: str, txn_id: str,
                            note: str) -> None:
        # An emptied note deletes the row rather than storing "", the same
        # as SQLite: a note he cleared is a note that is not there.
        if not note.strip():
            self._cur.execute(
                "delete from force_flow_notes where league_slug=%s and "
                "season=%s and txn_id=%s", (league, season, txn_id))
            return
        self._cur.execute(
            "insert into force_flow_notes (league_slug, season, txn_id, note, "
            "updated_at) values (%s,%s,%s,%s, now()) "
            "on conflict (league_slug, season, txn_id) do update "
            "set note=excluded.note, updated_at=now()",
            (league, season, txn_id, note.strip()))

    def add_rewrite_request(self, league: str, season: str, issue: str,
                            section: str, note: str) -> int:
        self._cur.execute(
            "insert into issue_revision_requests (league_slug, season, "
            "issue_key, section, note, created_at) "
            "values (%s,%s,%s,%s,%s, now()) returning id",
            (league, season, issue, section, note.strip()))
        return self._cur.fetchone()[0]

    def mark_sync_reviewed(self, league: str, season: str) -> str | None:
        self._cur.execute(
            "update sync_snapshots set reviewed_at = now() where snapshot_id = "
            "(select snapshot_id from sync_snapshots where league_slug=%s and "
            "season=%s order by taken_at desc limit 1) returning taken_at",
            (league, season))
        row = self._cur.fetchone()
        return str(row[0]) if row else None

    # -- the rewrite queue ---------------------------------------------

    def resolve_rewrite_requests(self, league: str, season: str, issue: str,
                                 section: str, status: str) -> None:
        self._cur.execute(
            "update issue_revision_requests set status=%s, resolved_at=now() "
            "where league_slug=%s and season=%s and issue_key=%s "
            "and section=%s and status='open'",
            (status, league, season, issue, section))
