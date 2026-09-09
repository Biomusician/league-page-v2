# Cutover readiness — the Commissioner's Desk on Postgres

Updated 2026-09-08, after the first production import attempt failed
part-written. Read the INCIDENT section at the foot of this file
before anything else.

**Status: READY IN SHAPE, PART-WRITTEN IN DATA. Nothing has been cut
over. `LEAGUEPAGE_PROSE_BACKEND` is unset, `.env` is untouched, the
filesystem is still authoritative, and nothing has been published or
deployed.**

**The first production import ran on 2026-09-08 and failed part-way.
Four of 808 rows are in the cloud and 804 are not. Nothing differs and
nothing was lost, but the importer's claim to be one transaction was
false at the time and is the reason those four stayed. It has been
repaired. See INCIDENT at the foot of this file.**

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
**4 of 808 metadata rows** -- the whole `issues` table, left behind by
the failed first attempt and byte-identical to local. The dry run now
reads:

```
804 rows to insert, 0 rows differ, 0 remote-only, prose identical
28 sections to mark commissioner-edited
type preflight: every value fits its destination column
```

**The corrected import has not been run.** Running it is a separate,
deliberate act, and it is the first thing a cutover needs.

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

---

## INCIDENT — the first production import failed part-written (2026-09-08)

**Status: the production import is INCOMPLETE. 4 of 808 rows are in the
cloud; 804 are not. Nothing was lost, nothing differs, and no cutover
happened. The importer has been repaired and is not to be re-run until
the gate at the end of this section is met.**

### What happened

Jonathan ran `scripts/import_editorial_state.py --apply`. It failed with:

```
psycopg.errors.DatatypeMismatch: column "included" is of type boolean
but expression is of type smallint
```

inside `cur.executemany()` on `issue_modules`, the second table in
`ORDER`. The first table, `issues`, had already been written.

### Root cause, and the second defect the first one exposed

**The datatype.** SQLite has no boolean type: it keeps `issue_modules`'s
`included` and `approved` as INTEGER 0/1 and hands them to Python as
ints. The Postgres columns are BOOLEAN, and psycopg does not guess that
an int was meant as a truth value -- correctly. Those are the only two
boolean columns anywhere in the import surface; the rest is 5 bigint, 13
integer, 99 text and 18 timestamptz.

**The transaction contract, which is the serious one.** `apply()` carried
the docstring *"One transaction. Every table or none of them."* It was
not true. `main()` opens the connection with `autocommit=True` so the
read-only planning can run, and under autocommit `with pg.cursor()` is
not a transaction. Probed against the live database rather than reasoned
about:

| under `autocommit=True`, inside a bare `with pg.cursor()` | result |
| --- | --- |
| `set_config('request.jwt.claims', ..., true)` | discarded -- claims read back EMPTY |
| `set local role authenticated` | discarded -- `current_role` stayed `postgres` |
| a statement following a committed one, then a failure | the earlier statement **survived** |

So two things were true at once and neither was written down:

1. Each table committed on its own. There was no rollback to have.
2. **The import never ran as the Commissioner.** It ran as the connection
   role, `postgres`, which has `rolbypassrls = true`. RLS did not apply to
   the import at all. The authorization boundary this script's own
   docstring claims was not weakened -- it was absent.

The RLS policy itself is fine and was verified live: `issues` has RLS
enabled AND forced with one `commissioner_all` policy; as the allowlisted
Commissioner the 4 rows are visible, as an authenticated non-Commissioner
0 rows are visible, and as `anon` the read is refused outright. The
import was simply never inside it.

### Exactly what survived

Verified three ways -- the importer's own dry run, `editorial_state_diff.py`,
and a column-by-column read of the raw values.

| table | local | cloud | identical | to create | differs | remote-only |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **issues** | 4 | **4** | **4** | 0 | 0 | 0 |
| issue_modules | 50 | 0 | 0 | 50 | 0 | 0 |
| prose_provenance | 8 | 0 | 0 | 8 | 0 | 0 |
| matchup_state | 13 | 0 | 0 | 13 | 0 | 0 |
| prose_revisions | 650 | 0 | 0 | 650 | 0 | 0 |
| issue_revision_requests | 1 | 0 | 0 | 1 | 0 | 0 |
| takes | 3 | 0 | 0 | 3 | 0 | 0 |
| story_decisions | 30 | 0 | 0 | 30 | 0 | 0 |
| award_decisions | 5 | 0 | 0 | 5 | 0 | 0 |
| power_rankings | 22 | 0 | 0 | 22 | 0 | 0 |
| team_names | 22 | 0 | 0 | 22 | 0 | 0 |
| force_flow_notes / editorial_usage / bit_usage | 0 | 0 | 0 | 0 | 0 | 0 |
| **TOTAL** | **808** | **4** | **4** | **804** | **0** | **0** |

The four survivors are the whole `issues` table:
`(disco, 2026, draft)`, `(disco, 2026, week-01)`, `(surfeit, 2026, draft)`,
`(surfeit, 2026, week-01)`. Every carried column matches local exactly.
`published_at`, the one cloud column nothing local fills, is NULL on all
four -- so they are complete rows, not half-written ones.

**This is outcome A.** The rows are kept. Deleting them to restore a
tidier notion of atomicity would be a write against production to make a
number look better, and the corrected importer already treats them as
`same` and will not touch them.

Everything else came through untouched:

- **prose: 34 local, 34 cloud, identical.** The import writes no prose;
  it verifies it and refuses if it has drifted.
- **section state: not reconciled.** All 34 cloud sections are still
  `generated`; 28 are still waiting to be marked commissioner-edited.
  That step runs after every table, so it never ran.
- **sequences: not advanced.** Four of the five have never been called.
  `prose_revisions_id_seq` sits at 84 from earlier live route tests whose
  rows were cleaned up; the import setvals it to `max(id)` afterwards, so
  it is inert.
- **no scratch rows** anywhere in the import tables.
- **local, `.env`, published snapshots, `LEAGUEPAGE_PROSE_BACKEND`:**
  untouched. No publication, no deployment, no cutover.

One number worth explaining: `editorial_state_diff.py` reports 798
local-only where the importer reports 804 to create. Neither is wrong.
The diff keys `prose_revisions` on its CONTENT
(`league_slug, season, issue_key, section, source, prior_text`), so six
revisions whose prior text is identical collapse into one another; the
importer keys on `id`, which is what it actually writes. **804 is the
number of rows the import will insert.**

### The repair

**Transaction.** `apply()` now runs inside `with pg.transaction()`, which
opens an explicit block even on an autocommit connection. That single
change makes both claims true at once: the LOCAL settings live for
exactly the write phase, and an exception anywhere rolls back every
table, the section-state reconciliation and the sequence setvals
together.

**Authorization.** `apply()` reads `current_role` back after setting it
and refuses to write if it is not `authenticated`. A rollback that runs
as the owner is still the wrong thing succeeding, and the failure mode it
guards against is silent by construction.

**Types.** A normalisation layer keyed on the DESTINATION Postgres type,
not on the column name. Only boolean is converted -- 0/1/True/False/NULL
and nothing else, because coercing by truthiness would turn a 2, an empty
string or the word "false" into an answer the script invented about data
nobody has looked at. Everything else passes through untouched.

**Preflight.** Every value that would be written is checked against the
column it would land in, on the dry run as well as the real one, before
the first write. A type problem is now `REFUSING BEFORE WRITE` with a
table, a column and a destination type on it. It never prints the value
of a `text` column: those hold prose, baseline drafts and private notes,
and a preflight failure is not a reason to put any of them on a terminal.

### What proves it

`tests/test_import_atomicity.py` (live, opt-in) fails the import at every
seam it has -- first table, middle table, last table, before the
section-state update, during it, and at the sequence setvals -- and
requires the same answer each time: every count exactly as before, the
section still `generated`, the sequence unmoved. It also pins the actor
during a real write and shows a non-Commissioner cannot run the import at
all. `tests/test_import_types.py` pins the conversion, the preflight and
the role guard with no database at all, so the regression cannot hide
behind an unset opt-in.

### The gate before the next attempt

The corrected `--apply` has NOT been run, and this tranche did not run
it. It may be run when, and only when: `differs = 0`, `remote-only = 0`,
the surviving rows are understood, the transaction tests are green and
the type preflight is green. All five are currently true.
