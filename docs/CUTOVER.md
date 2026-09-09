# Cutover readiness — the Commissioner's Desk on Postgres

Updated 2026-09-09, after migration 0007 was applied and verified, About
was wired to the cloud, and a second Supabase project was found where
there should have been one. The INCIDENT section at the foot of this file
records the import attempt that failed first; it is history now.

**Status: READY IN DATA. Nothing has been cut over.
`LEAGUEPAGE_PROSE_BACKEND` is unset, `.env` is untouched, the filesystem
is still authoritative, and nothing has been published or deployed.**

**The corrected import ran on 2026-09-09 and completed: 832 rows in one
transaction, on top of the 4 that survived the first attempt. The cloud
and this machine now hold the same editorial state, verified three ways
and reported below. That is what READY IN DATA means and it is all it
means.**

**READY FOR CUTOVER is a different claim and is NOT made yet.** Two
manual steps are open and both are Jonathan's: **Auth has to move to the
canonical project**, and `migrations/0008_least_privilege.sql` has to be
applied. Until Auth is consolidated the remaining verification gates
cannot honestly be run. See "0007 applied, About wired, and one
configuration that was wrong" below.

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

### The data has moved, and it matches

The cloud holds **34 prose sections and all 808 metadata rows**, and
every one of them matches this machine. The import is done. Re-running
the dry run now reads:

```
0 rows to insert, 0 rows differ, 0 cloud-only, prose identical
0 sections to mark commissioner-edited, 28 already right
type preflight: every value fits its destination column
```

Verified three ways rather than taken from the apply message, all on
2026-09-09:

| check | result |
| --- | --- |
| `import_editorial_state.py` (dry run) | insert 0, differs 0, cloud-only 0 |
| `editorial_state_diff.py` | 842 local, 842 cloud, 0 local-only, 0 cloud-only, 0 differs |
| `prose_tool.py verify` | same 34, filesystem-only 0, postgres-only 0, content-differs 0 |

Per table, local and cloud: issues 4/4, issue_modules 50/50,
prose_provenance 8/8, matchup_state 13/13, prose_revisions 650/650,
issue_revision_requests 1/1, takes 3/3, story_decisions 30/30,
award_decisions 5/5, power_rankings 22/22, team_names 22/22, and
force_flow_notes, editorial_usage and bit_usage empty on both sides
because they are empty here.

**Section state reconciled exactly.** 28 sections carry
`commissioner-edited` in both stores, section for section. The other 6
have no local state row and remain `generated`: two `all-city` sections
in disco week-01, and four surfeit proposals. The absence of a local row
is not a record of anything, and the import treats it as such.

**Legacy approvals came through as history, not as claims.** 19 approved
modules, all 19 with `approved_sha` NULL on both sides -- they were
approved before signatures existed. The Desk already reads that state
correctly and says so: the card shows "approved before signatures" and
the rail shows "approved, unsigned" rather than a plain green "approved",
which is the one state that would stop him looking. Nothing was
signed, repaired or re-approved during the migration.
`matchup_state.covered_sha` is NULL on all 13 rows on both sides, as it
was.

The only representational difference anywhere is timestamps: SQLite keeps
them as ISO strings and Postgres as `timestamptz`. Same instant, and the
diff normalises them, which is why it reports 0.

**Nothing has been cut over.** Having the data in the cloud is a
precondition for a cutover, not a cutover.

---

## What is not ready

### Every existing approval is legacy, and the migration kept it that way

All 19 approved modules on this machine carry `approved=1` with **no
signature**, and all 13 matchup rows carry no `covered_sha`. Signatures
arrived in 5A and nothing has been approved since.

The import copied those rows exactly as they stand, nulls included --
verified after the fact on 2026-09-09, both sides, key for key. Computing
a signature during a migration would have manufactured a claim the
Commissioner never made, about text he may never have read, and it would
be indistinguishable afterwards from one he did make.

So the consequence is worth stating plainly: **those 19 modules read as
not-approved, and he re-approves them once.** That is not something a
cutover would introduce -- it has been true on this machine since 5A,
because `module_approved` treats a recorded approval with no signature
as a historical fact rather than as approval of what is there now. It
does not touch published history: a snapshot is an immutable file and
never consults this.

### `about_save` — decided, migration written, not applied

The About page's copy is still a file, and it is the only authoritative
prose outside the store. The question this section used to leave open --
a key shape for site copy, or a table of its own -- is now answered: a
table of its own, `site_documents`, in
`migrations/0007_site_documents.sql`.

**On a hosted Desk today the About editor still would not work**, because
the migration has not been applied and the store is not wired. Both steps,
and what each one blocks, are in "The last filesystem write" below.

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

- The About page editor, until `0007` is applied and the store is wired
  (see "The last filesystem write" below).
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

## The last filesystem write, and the migration that closed it

**Superseded on 2026-09-09: 0007 is applied and About is wired.
Kept for the reasoning, which is still the reasoning. The
current state is in the section after this one.**

**Site -> About is the one authoring surface still writing authoritative
state to this machine.** Every other route expresses intent to
`EditorialStore` and lands in a table. About persists to
`editorial/site/about.md`, and `leaguepage/site_build.py` reads the same
file when it builds `about/index.html`. On a hosted Desk that file does
not survive a restart, so the page describing how the paper is made --
and disclosing the AI assistance -- is the one thing Hosted Beta would
silently discard.

`migrations/0007_site_documents.sql` closes it with a table of its own:

```
site_documents (slug text primary key, body text, updated_at, updated_by)
```

Why not one of the tables already here, and what it deliberately is not,
are in `docs/DECISIONS.md` under 2026-09-09. The short version: `sections`
is addressed by a ProseKey and About has no league, season or issue, so
storing it there means inventing three values in the primary key the whole
editorial model is addressed by. `research_artifacts` is issue-scoped
research. `editorial_meta` is recomputable settings the parity diff
deliberately skips, which makes it the wrong home for authored prose. And
this is not a CMS: no revisions, no drafts, no workflow, no per-league
copy.

One thing is consciously given up. On the filesystem About has git
history; in `site_documents` it has `updated_at` and `updated_by` and no
revision log. That is a real reduction, it is chosen rather than
overlooked, and reversing it is a new decision with a new table.

### MANUAL MIGRATION REQUIRED (done: 0007 was applied 2026-09-09)

**`migrations/0007_site_documents.sql` has NOT been applied.** Apply it
the same way as the others -- Supabase dashboard, SQL Editor, New query,
paste the file, Run. Expect "Success. No rows returned". It is additive
and idempotent, so running it twice is a no-op, and it raises rather than
returning quietly if RLS is not enabled AND forced, if the
`commissioner_all` policy is missing, or if anon holds any grant on the
table.

Two things confirm it landed:

```
.venv\Scripts\python.exe scripts\verify_supabase_schema.py
```

-- `site_documents` moves off the "genuinely absent" list -- and the eight
live tests in `tests/test_site_documents_rls.py`, which skip with the
reason "migration 0007 has not been applied to this database yet" until
it is, and then assert RLS, the single policy, anon's total absence of
grants, a Commissioner round trip, a stranger seeing zero rows and being
refused a write, anon refused outright, and the migration being
re-runnable over existing content without destroying it.

**The application wiring was deliberately not written yet.** It is now:
`site_documents.read()` and `EditorialState.set_site_document` went in
once the table existed to exercise them against.

### What this blocks

These gates cannot be honestly run until 0007 is applied and About is
wired:

- real HTTP route verification against imported data, including the About
  save
- the fault and concurrency gates for About
- a rebuilt route safety table with an accurate hosted-safe count
- the full Postgres parity harness, negative control and privacy sweep
  against a complete schema
- the cloud -> local rollback proof, which has to carry About back to
  `editorial/site/about.md` losslessly

So: **READY IN DATA, not READY FOR CUTOVER.**

---

## 0007 applied, About wired, and one configuration that was wrong

Updated 2026-09-09.

### Migration 0007 is live, checked rather than believed

The SQL Editor said "Success". That is not evidence, so the table was
asked directly on the connection the application itself uses.

| claim | result |
| --- | --- |
| table exists | `to_regclass('public.site_documents')` -> `site_documents` |
| columns, in order | `slug, body, updated_at, updated_by` |
| types | `text, text, timestamptz, text` |
| primary key | `slug` |
| `body` | NOT NULL, default `''::text` |
| `updated_at` | NOT NULL, default `now()` |
| `updated_by` | nullable, no default |
| RLS | enabled **and** forced |
| policies | exactly one: `commissioner_all`, `ALL`, `{authenticated}`, `app_is_commissioner()` on both USING and WITH CHECK |
| anon grants | none |
| `leaguepage_app` grants | SELECT, INSERT, UPDATE, DELETE |
| re-running the migration | idempotent, and existing content survives |

Authorization proved three ways, none of them using the owner connection
as evidence: **anon** is refused outright (it holds no grant, so the
failure is a permission error before any policy is consulted); an
**authenticated stranger** sees zero rows and cannot write; the
**allowlisted Commissioner** round-trips insert, select, update, delete.
`tests/test_site_documents_rls.py`, 8 tests, all green.

### One thing was NOT as the migrations claim: `authenticated` grants

`authenticated` holds **TRUNCATE, REFERENCES and TRIGGER** on all 22
tables, on top of the four the migrations grant. This is not 0007's doing
-- it is uniform across every table since 0001, and it comes from the
Supabase project default that grants ALL on every new table in `public`
to `anon`, `authenticated` and `service_role`. The migrations revoked
anon. Nothing revoked the surplus from the role that actually writes.

TRUNCATE is the one that matters, because **row level security does not
apply to TRUNCATE**. `commissioner_all` stops a non-Commissioner deleting
a row and would not stop the same role emptying the table.

How exposed is it today: not. Acting as `authenticated` needs a Postgres
connection, which needs the DSN, or PostgREST with a user JWT -- and
PostgREST only ever issues SELECT/INSERT/UPDATE/DELETE. It cannot TRUNCATE
and cannot create a trigger. So this is the gap between what the
migrations say they grant and what the database grants, worth closing
before a hosted Desk exists rather than after.

**`migrations/0008_least_privilege.sql` closes it** and has NOT been
applied. It states the whole intended set rather than naming the surplus
(`revoke all` then `grant select, insert, update, delete`), which is
idempotent and does not need to track MAINTAIN arriving in PostgreSQL 17;
it fixes the default privileges too, so the next table created does not
re-acquire it; and it raises rather than returning if any table still
grants more than CRUD afterwards. The five live tests in
`tests/test_least_privilege.py` skip with that reason until it is applied.

### Site -> About now goes where the backend says

`site_documents` has a store, and it is the smallest one that could work:
`EditorialState.site_document` / `set_site_document`, the same shape
`research_artifacts` already uses for a thing that is a file here and a
row there.

- **filesystem mode**: `editorial/site/about.md`, exactly as before, with
  `DEFAULT_ABOUT` as the fallback when the file does not exist.
- **postgres mode**: `site_documents` where `slug = 'about'`, with the
  same fallback when there is no row, and **no file written at all** --
  proved by a test that asserts the editorial tree stays empty.
- No dual write, and no second code path: the route calls
  `act.state.set_site_document(...)` inside a store action, so on Postgres
  the About save is one transaction as the signed-in Commissioner with RLS
  applying to it, like every other authoring route.

`read()` takes no actor, because the public site build reads it too --
the same shape an unbound prose repository already has for the build's
prose.

The editor names where the copy is kept: a path on the filesystem, the
table on Postgres, and never a DSN. `tests/test_about_store.py` covers the
default, the save, an update, survival across a restart, `updated_by`
carrying the Commissioner, `updated_at` moving, the stranger refused, an
action with no identity refused, and the preview writing nothing.

**The route table moved with it.** `about_save` is now `owner="store"`,
`kind="authoring"`, `cloud=("site_documents",)` and -- the interesting
part -- **`local=()`**: it is the first authoring route with no local
remainder at all. It is still not hosted-safe, because the selected
backend today is the filesystem and that is where the write lands. The
reason is now the setting rather than the code.

### The route audit was lying, and the safety table is derived from it

`scripts/audit_route_writes.py` reported `about_preview` -- four lines,
returns JSON, writes nothing -- as writing files. It resolved call targets
by BARE NAME, and `leaguepage` has three `render`s: `prose.render`, which
`about_preview` calls; a nested helper inside `site_build.build`, which
writes pages; and a method on a writing packet.

It now resolves before it follows: a module alias is read from the calling
module's own imports, so `prose.render` is `prose.py` and nothing else; a
bare name resolves to the module's own definition, then to a `from ...
import`, then -- only if the name is unique in the whole package -- to
that; and a method call on an object whose type the AST cannot know
follows every METHOD of that name, which is the wide net working as
intended, but never a module-level or nested function that merely shares
it. Anything left is printed on an `unresolved:` line rather than dropped,
because a pass that quietly stops following calls under-reports.

Nine route rows changed, every one of them a reduction, and every one
corroborated before it was accepted -- four against
`test_hosted_mutation_audit.py`, which drives the routes and records what
they actually write, and the rest by reading the code:

| route | was reported | actually |
| --- | --- | --- |
| `about_preview` | writes files | writes nothing |
| `editor_approve` | `set_issue_status` | writes `issue_modules`, `matchup_state` |
| `lowdown_save` | `set_issue_status` | prose plus `section_prose_state`, `issue_modules` |
| `issue_build` | `set_meta` | `set_issue_status` only |
| `issue_publish` | four file writes | the snapshot only; the HTML is the site build's |
| `sync_start` | file writes | none |
| `publish_start`, `qa_action` | extra aliases of the same write | the writes they have |

The current audit reports **45 mutating routes, 0 unresolved calls**.

### The two-Supabase-project split

`DATABASE_URL` and `SUPABASE_URL` pointed at **different Supabase
projects**, and nothing in the code, the tests or these documents said
they were supposed to match.

| setting | project | responsibility |
| --- | --- | --- |
| `DATABASE_URL` | `mxlrmjapffjplxtnuzkd` | every migration 0001-0007; all 842 imported editorial rows; `app_commissioners`; every RLS policy; the prose repository, the editorial store, the importer, the diff, the schema verifier's direct check |
| `SUPABASE_URL` + `SUPABASE_PUBLISHABLE_KEY` | `kgxmdhkswdhjbcozcznv` | OTP initiation (`/auth/v1/otp`), OTP verification (`/auth/v1/verify`), the health probe (`/auth/v1/settings`). Carries 0001's tables and nothing since |
| `SUPABASE_SECRET_KEY` | not set anywhere | unused. Nothing in the application reads it |
| `LEAGUEPAGE_COMMISSIONER_EMAILS` | local `.env` | the allowlist checked before an OTP is sent and again after it is verified |

**Be precise about what it broke, because the obvious reading is wrong.**
`app_is_commissioner()` reads `auth.jwt() ->> 'email'`, and the value
there is the one **this application asserts** with
`set_config('request.jwt.claims', ...)` from its own session. The database
never verifies a Supabase-issued token. So the mismatch never let anyone
past RLS, and no editorial row was ever at risk.

What it did mean is that authentication and authorization lived in two
projects with nothing linking them. The allowlist that decides access is a
table in project A; the accounts that vouch for people are in project B;
and every operational question then has two answers -- which dashboard
shows the sign-ins, whose rate limits apply, where to disable a
compromised account.

**And it made a diagnostic lie.** `verify_supabase_schema.py` asks
PostgREST over `SUPABASE_URL` and Postgres over `DATABASE_URL`. For weeks
it reported six tables as "NOT VISIBLE TO PostgREST" and explained it as a
schema cache "pinned to an older snapshot". They were not cached; they
were **in the other project, which has never had them**. That explanation
was recorded as fact in this document and in `docs/HANDOFF.md`, and both
are corrected. The script now prints both project refs at the top, says
`*** THESE ARE DIFFERENT PROJECTS ***` when they differ, and refuses to
offer the cache explanation in that case.

**It cannot happen silently again.** `leaguepage/project_check.py` derives
each project's ref -- from the DSN's username for a pooled connection,
from the hostname for a direct one, from the subdomain for the API URL --
and compares them. A mismatch **raises at startup** when the two halves
are actually live (a Postgres backend, or sign-in switched on) and warns
otherwise, because the local filesystem Desk signs nobody in and reads its
own disk, and a config error an operator cannot act on without stopping
work is one that gets worked around. `/health` reports the verdict word
only -- never a ref, never a host, never a DSN -- and the mismatch message
names the two refs, which are public, and nothing else.
`tests/test_project_consistency.py`, 23 tests, including the pooler shape
that a hostname-only check would have passed.

### MANUAL AUTH CONSOLIDATION STEP REQUIRED

The canonical project is **`mxlrmjapffjplxtnuzkd`** -- the one
`DATABASE_URL` points at, holding every migration and all 842 rows. Auth
moves to it. Nothing about this can be done from here: the publishable key
lives in that project's dashboard.

What was established rather than assumed:

- the canonical project **has Auth provisioned**: `/auth/v1/health` there
  answers `401 "No API key found"`, which is the endpoint asking for a key
  rather than the endpoint not existing.
- its `auth` schema exists and **`auth.users` has 0 rows**, so there is
  no account to migrate and none to collide with.
- `app_commissioners` in the canonical project holds **exactly one row**,
  and it is **the same address** as `LEAGUEPAGE_COMMISSIONER_EMAILS`. The
  allowlist is already correct for it; nothing needs adding.
- **no user migration is needed.** `send_email_otp` posts
  `should_create_user: True`, which exists for precisely this case -- a
  brand-new project has no user record, and Supabase answers
  `otp_disabled / Signups not allowed for otp` without it. The account is
  created on first sign-in, and permission to use the application is
  still decided twice by us: the allowlist before the code is sent and
  again against the address Supabase returns, then RLS against
  `app_commissioners`.
- **no Redirect URL and no Site URL are needed.** The flow posts to
  `/auth/v1/otp` and `/auth/v1/verify` with a payload of
  `{"email": ..., "options": {"should_create_user": true}}` -- no
  `redirect_to` anywhere. Magic links would need them; a six-digit code
  does not.
- the settings to match, read from the current auth project:
  `external.email = true`, `disable_signup = false`,
  `mailer_autoconfirm = false`.

**The steps, in order:**

1. Open the dashboard for project **`mxlrmjapffjplxtnuzkd`** ->
   Authentication -> Providers -> **Email**. Confirm it is enabled and
   that sign-ups are allowed. Leave "Confirm email" as it is; the OTP
   flow does not use a confirmation link.
2. Settings -> API. Copy that project's **Project URL** and its
   **publishable / anon key**.
3. Put them in `.env` as `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`,
   replacing the `kgxmdhkswdhjbcozcznv` values. **This tranche did not
   edit `.env`.**
4. Check it: `.venv\\Scripts\\python.exe scripts\\check_supabase.py`, then
   `.venv\\Scripts\\python.exe scripts\\verify_supabase_schema.py` -- the
   two project lines at the top must now be the same ref and the
   `*** THESE ARE DIFFERENT PROJECTS ***` banner must be gone.
5. Sign in once with `LEAGUEPAGE_AUTH_MODE=required` so the account is
   created in the canonical project. Supabase's built-in mailer sends the
   code and has a low hourly limit on the free tier, so expect to wait if
   you retry.
6. The old project `kgxmdhkswdhjbcozcznv` then holds 0001's empty tables
   and one stale account. Retiring it is a separate decision; nothing
   reads it once step 3 is done.

Until step 3, the startup guard raises for any configuration that puts
both halves in play, so a hosted or Postgres-backed Desk **cannot be
started against the split** even by accident.

### What is still not proved

Everything in section 8 of the brief, and it is gated on the step above
rather than on anything in the code: real HTTP authoring routes across the
full surface on imported Postgres state, the fault and concurrency gates,
the rebuilt hosted-safe count, the full parity harness, 27/27 assembly,
preview parity, the negative control, the privacy sweep, and the cloud ->
fresh-local rollback proof including `site_documents/about` ->
`editorial/site/about.md`.

What IS proved of the chain: **from a signed Desk session downwards.**
`tests/test_auth_chain_end_to_end.py` drives a real login token through
`/auth/callback`, takes the signed session cookie, and observes -- inside
the writing transaction, because outside it the connection is the owner
and always was -- that `current_role` is `authenticated`, that
`request.jwt.claims` carries the Commissioner's address, that
`app_is_commissioner()` returns true, and that the row lands. The link
above it, Supabase minting that identity **in the canonical project**, is
the manual step.

**Status: READY IN DATA. Not READY FOR CUTOVER.**

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

### How it ended

The gate on a second attempt was: `differs = 0`, `remote-only = 0`, the
surviving rows understood, the transaction tests green, the type
preflight green. All five held, and Jonathan ran the corrected
`--apply` on 2026-09-09. It wrote 832 rows in one transaction -- 804
metadata rows, the 28 section-state reconciliations -- and did not
rewrite the 4 `issues` rows, which were already identical. The
verification is at the top of this file under "The data has moved, and
it matches"; this section stays as the record of how it failed the first
time.
