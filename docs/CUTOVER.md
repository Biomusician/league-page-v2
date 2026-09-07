# Cutover readiness — the Commissioner's Desk on Postgres

Updated 2026-09-07, end of Tranche 5C.

**Status: READY IN SHAPE, NOT IN DATA. Nothing has been cut over.
`LEAGUEPAGE_PROSE_BACKEND` is unset, `.env` is untouched, the filesystem
is still authoritative, and nothing has been published or deployed.**

This document is the thing to read before deciding. It says what is
actually true, what is still missing, exactly what a cutover would
involve, and exactly how to undo it.

---

## What is ready

**Every authoring route has given up its own transaction.** All 35 of
them express intent to `EditorialStore` and write nothing themselves.
That is proved two ways, because either alone is worth less than it
looks:

- structurally, by reading each route's source and refusing it if it
  still contains a direct write (`tests/test_prose_routes_are_store_owned.py`)
- behaviourally, by breaking a metadata seam mid-route and checking what
  survived

The second one is the point. A route can call the store and still write
beside it, and the difference only shows when something fails halfway.

**The store keeps its contract against the real database.** Two live
suites, no skips:

| suite | what it drives | result |
| --- | --- | --- |
| `tests/test_editorial_store.py` | the store, called directly | 39 passed |
| `tests/test_routes_on_postgres.py` | the real routes over HTTP | 10 passed |

The second is new in this tranche and is the one that answers "do the
routes actually use it". It builds the application with the Postgres
backend selected **in that process only**, writes into a scratch
namespace nothing else uses, and deletes it either side.

**The difference a cutover buys, measured rather than asserted:**

| seam broken mid-route | filesystem today | Postgres |
| --- | --- | --- |
| prose state write fails | words land, nothing describes them | neither |
| provenance write fails | words land, nothing describes them | neither |
| CTP approval part-written | module row stands, coverage half-written | neither |
| angle selected, decision fails | preview says ready-to-draft, no decision | neither |

**Schema.** Migrations 0003 and 0006 are applied and verified live --
columns, types, defaults, indexes including partial predicates, RLS
enabled AND forced, one `commissioner_all` policy, no anon grant.

**The port.** 30 operations, two adapters, both complete. Research is
included: `rough-lowdown.md` is a file here and a `research_artifacts`
row in the cloud, because it is read on a path that decides what gets
written and a hosted Desk would otherwise answer "no draft, no AI help"
for every section, silently.

---

## What is not ready

### The data has not moved

The cloud holds **34 prose sections, identical to this machine's**, and
**no metadata at all**. `scripts/import_editorial_state.py` is written
and its dry run is clean:

```
808 rows to insert, 0 rows differ, prose identical
28 sections to mark commissioner-edited
```

**It has not been run.** Running it is a separate, deliberate act, and
it is the first thing a cutover needs.

### Every existing approval is legacy, and the migration must keep it that way

All 19 approved modules on this machine carry `approved=1` with **no
signature**, and all 13 matchup rows carry no `covered_sha`. Signatures
arrived in 5A and nothing has been approved since.

The import copies those rows exactly as they stand, including the nulls.
Computing a signature during a migration would manufacture a claim the
Commissioner never made, about text he may never have read, and it would
be indistinguishable afterwards from one he did make.

So the consequence is worth stating plainly: **those 19 modules read as
not-approved, and he re-approves them once.** That is not something a
cutover would introduce -- it has been true on this machine since 5A,
because `module_approved` treats a recorded approval with no signature
as a historical fact rather than as approval of what is there now. It
does not touch published history: a snapshot is an immutable file and
never consults this.

### `about_save` — an open gap, in scope

The About page's copy is still a file, and it is the only authoritative
prose outside the store. The blocker is specific rather than lazy: a
`ProseKey` is `(league, season, issue, section)` and a site-wide About
page has none of those. It needs either a key shape for site copy or a
table of its own, and inventing a third way to store words inside a
migration tranche is how a schema acquires one permanently.

**On a hosted Desk today, the About editor would not work.** That is
recorded as a missing feature, not as reduced scope.

### Research beyond the rough draft

`PREP.md`, `AUTHORING.md`, `themes.md` and `outline.md` are read by
screens that only display them. They can follow the same port when the
hosted Desk needs them. Only the rough draft changes what gets written,
so only the rough draft moved.

### `issue_build` is local by design

It writes recomputable research onto a filesystem, and it is a step in a
Claude Code authoring session that already needs the repository on the
machine. Nothing it writes is authoritative. This is a resolution, not a
gap.

---

## What a cutover would actually be

1. Run `scripts/import_editorial_state.py` (dry run first, then
   `--apply`). It writes to the cloud in one transaction, as the
   signed-in Commissioner, so RLS applies to it exactly as it does to
   the Desk.
2. Re-run `scripts/editorial_state_diff.py` and confirm the two sides
   agree.
3. Set `LEAGUEPAGE_PROSE_BACKEND=postgres`.
4. Restart the Desk.

That is the whole of it, and steps 3 and 4 are the only irreversible-
feeling ones -- which they are not, see below.

## What rolling back would be

Unset `LEAGUEPAGE_PROSE_BACKEND` and restart.

That is genuinely all, and the reason is proved rather than asserted:
**the import writes to the cloud and to nothing else.**
`tests/test_import_leaves_this_machine_alone.py` watches every SQLite
statement and every write under the editorial tree while the import
runs and requires the count to be zero -- not "no important writes",
zero. So the local store after an import is byte-identical to the local
store before it, and rolling back returns to exactly the place that was
left.

The one thing rollback does not recover is **anything authored in the
cloud after the cutover**. From the moment the Desk is writing to
Postgres, going back to the filesystem means going back to the state as
of the cutover. That is the real decision, and it is a decision about
when, not about whether.

---

## What would break if it were cut over today

- The About page editor (see above).
- Nothing else that the live route suite covers.

The honest caveat is what that suite does NOT cover: it drives the
authoring routes, not every screen. Screens that only read still read
analytics from SQLite, which is correct -- Sleeper data is a cache a
hosted Desk would sync for itself -- but a hosted deployment would need
that sync to have run.

---

## The gate

Cutover is **allowed to be ready**. It is **not allowed to happen**
without Jonathan saying so explicitly, and this tranche did not switch
anything.

`tests/test_hosted_mutation_audit.py` holds the gate as a test rather
than a paragraph: every authoring route still claims `safe=False`,
because hosted safety is about where the write LANDS and it still lands
here. Being store-owned is the shape a cutover needs and is not a
cutover, and
`test_store_owned_is_not_the_same_claim_as_hosted_safe` exists to stop
those two being quietly conflated.
