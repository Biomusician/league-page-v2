# Commissioner Portal — target architecture

How the Desk gets from a localhost tool to an authenticated portal the
Commissioner can open from another device, without a rewrite and without
building a SaaS. Written 2026-09-05, at the start of that work.

Three sections: **CURRENT** is what runs today, **TRANSITION** is the
ordered work, **TARGET** is where it lands. Manual gates — things only
Jonathan can do — are listed separately at the end, because code cannot
close them and pretending otherwise wastes a session.

---

## CURRENT

A FastAPI app on `localhost:8026`, started from a desktop shortcut, over a
SQLite database and a tree of Markdown files.

| Concern | Today |
| --- | --- |
| Identity | Supabase email OTP; `LEAGUEPAGE_AUTH_MODE=off` on localhost, `required` elsewhere |
| Authorization | Local allowlist (`LEAGUEPAGE_COMMISSIONER_EMAILS`) plus the Postgres `app_commissioners` table that RLS checks |
| Sessions | HMAC-signed cookies, `LEAGUEPAGE_SECRET_KEY` |
| CSRF | Middleware over every mutating method; token in a `<meta>` tag, attached centrally by `static/desk.js` |
| Prose | Behind `ProseRepository` (`leaguepage/prose_store.py`). Filesystem backend authoritative; Postgres implemented and not live |
| Editorial state | SQLite: decisions, approvals, provenance, prose revisions, takes, notes |
| Jobs | Durable `jobs` + `job_events` rows with leases (`leaguepage/jobs.py`); a local daemon thread is one possible worker, not the record |
| Publication | Immutable JSON snapshots in `published/`, corrections as sibling revisions |
| Build | Local `build_public_site.py` into `dist/`, privacy audit, then the `site` branch or the Vercel CLI |

The parts worth keeping are the invariants, not the plumbing: immutable
snapshots, correction-not-overwrite, the privacy audit as a build gate,
approval bound to content, provenance recorded rather than inferred.

### What blocks a hosted deployment today

1. ~~**Prose is filesystem state.**~~ **Done, 2026-09-05.** Every
   Commissioner-editable prose read and write goes through
   `ProseRepository`. Nine production write sites became repository calls;
   the filesystem backend is still authoritative, and a Postgres backend
   implementing the same contract exists and is not live. What remains of
   this blocker is the four CLI correction scripts that edit Markdown
   directly: they now refuse to run unless the filesystem is authoritative,
   so they cannot create a split brain, but they would need migrating
   before they are useful again after a cutover.
2. ~~**Jobs are process globals.**~~ **Done, 2026-09-05.** Job state is
   durable and leased (see *Durable jobs*, below). ~~What remains is the
   auth dictionaries.~~ **Analysed and closed, 2026-09-06** — see *The
   auth residual*, below. `_EPHEMERAL` already fails closed; the rate
   limiter is a courtesy brake in front of Supabase's own; and the one
   real property, single-use redemption, was unreachable hosted and is
   now refused outright rather than left to an implicit invariant.
3. **The build reads the private database.** `dist/` is produced from
   SQLite and `editorial/`, which is why Vercel never rebuilds and only
   ever receives an audited artifact.
4. ~~**`app_commissioners` is empty.**~~ **Seeded 2026-09-06**, by
   Jonathan, with owner rights. One row, matching
   `LEAGUEPAGE_COMMISSIONER_EMAILS` exactly. Proved by assuming each
   application role (below), not by reading it as owner.
5. **Editorial state is in two databases.** Prose can move to Postgres —
   proved end to end on 2026-09-06, against the real database, with real
   prose. The seven pieces of metadata that a single Commissioner click
   writes alongside it cannot. This is the live blocker and it is why the
   filesystem is still authoritative — see *The cutover boundary*, below.
6. **The Postgres backend connects as the database owner.** `postgres`
   carries `BYPASSRLS`, so the RLS model that migration 0001 describes
   protects the browser and does not protect the application's own data
   path. That is defensible for a single-tenant Desk whose authorization
   is `leaguepage/auth.py`, but it is not what the migration's own
   comments claim, and it should be decided rather than inherited.

---

## TRANSITION

Ordered so that nothing built early is thrown away later. Each step is
useful on its own.

**1. Canonical preview.** *(done, 2026-09-05)* The private preview renders
through `public/issue_page.html` from a snapshot-shaped dict. One renderer,
so the preview cannot lie about the page.

**2. Issue Room.** *(done, 2026-09-05)* One weekly workspace over the
existing context and endpoints. It is a template and a rail; when prose
moves to Postgres the room does not change, because it never touches the
filesystem itself.

**3. CTP approval.** *(done, 2026-09-05)* One approval over the published
unit, signed over the previews it covers. Removes six clicks a week and
makes "approved" mean a particular text, which is what the publish gate
needs when two devices are editing.

**4. Repository boundaries.** `ProseRepository` first: `read(section)`,
`write(section, text, expected_version)`, `history(section)`,
`restore(revision)`. The filesystem implementation is what exists now; the
Postgres one is a second implementation, not a rewrite. Only prose and
jobs get repositories — abstracting every `SELECT` in `Storage` for
theoretical purity would cost more than the cutover.

**5. Durable jobs.** A `jobs` table with `queued | running | succeeded |
failed`, stages and progress, replacing the process globals. Needed before
anything runs where a process can die mid-request, and useful locally
immediately: a publish survives a Desk restart.

**6. Cloud persistence.** Prose and editorial state in Supabase Postgres,
with export back to a repo-shaped Markdown bundle so the local path and
the backups keep working. The DB becomes the source of truth; git keeps
being the archive.

**7. Hosted beta.** A private Vercel project with `LEAGUEPAGE_AUTH_MODE=required`.
Read and write editorial state; publication still runs locally at first.

**8. Publication worker.** Only after 5 and 6. See below.

---

## TARGET

    Commissioner (any device)
        │  Supabase OTP → session cookie → allowlist + RLS
        ▼
    Private Vercel app  ── reads/writes ──►  Supabase Postgres
        │                                     prose, decisions, provenance,
        │                                     approvals, jobs
        │  enqueue publish job
        ▼
    Worker (GitHub Actions, workflow_dispatch)
        │  reads the frozen snapshot for one revision
        │  builds dist/, runs the privacy audit
        │  pushes the `site` branch
        ▼
    Vercel production ──► readers

### Why a worker, not a request handler

A publish is sync → snapshot → build a hundred pages → privacy audit →
deploy → verify. That is minutes, not milliseconds, and a serverless
request handler that dies halfway leaves no record of where it got to.
GitHub Actions is the strong candidate because the repository is already
there and Vercel already consumes the `site` branch from it.

**Red team, before any of it is built:**

- *Secrets.* The source repository is private (`docs/DEPLOY.md`). The
  workflow needs a Vercel token and Supabase service credentials; they live
  in Actions secrets, never in the built artifact, and the privacy audit
  already fails a build that contains a credential shape. If the repository
  ever became public this review has to be redone, because a workflow that
  can be triggered from a fork is a different threat model.
- *Double dispatch.* Two clicks must not produce two deployments. The job
  row is the lock: a publish is claimed by id, and the workflow refuses to
  start when a job for that issue is already running.
- *Which revision shipped.* The worker builds one named revision, not
  "current state", and records the deployment id against it. This already
  exists locally as `deploy_state:{league}:{season}:{issue}` carrying the
  revision it shipped.
- *Partial deployment.* The build is atomic at the `site` branch: nothing
  is pushed unless the build and audit both pass, which is how
  `push_site_branch.py` behaves today.
- *Losing the local path.* The local build stays supported. If the cloud
  path fails, publishing from this machine must still work — that is the
  recovery route, and it is also the thing that proves the artifact is the
  same either way.

### Durable jobs

Built 2026-09-05. `leaguepage/jobs.py` is the control plane and
`leaguepage/job_runner.py` is the executor, and the split is what makes the
worker above a change of caller rather than a rewrite.

| Piece | What it is for |
| --- | --- |
| `jobs` row | What was requested, which immutable revision it applies to, who holds the lease, how it ended |
| `job_events` | Append-only stage history. The rendered stage list is a fold over it; the history the fold discards is what a recovery reads |
| Lease | `lease_owner` + `lease_expires_at`, renewed by a heartbeat every 30s. Every write is guarded by the owner, so a superseded worker cannot overwrite its replacement |
| `idempotency_key` | Held only while a job is live, released when it ends. A partial unique index enforces it |
| `target_revision` | Binds a publish to the revision its snapshot froze. Rebinding to a different number is refused |
| `error_code` | Stable slug for branching; `error` is the sentence a person reads; the log file carries the diagnosis |

`JobRepository` is the seam. It is deliberately free of SQL, connections
and datetime objects so `PostgresJobRepository` can implement it exactly;
a test asserts the SQLite implementation matches the protocol
signature-for-signature. `migrations/0004_durable_jobs.sql` brings the
Postgres side to the same shape.

Two states are new and both are load-bearing. **queued** exists because a
job can be created by a request handler and claimed by something else, so
the UI asks `job.active` rather than `state == "running"`. **lost** exists
because a worker that stops renewing its lease has not failed, it has
stopped reporting, and those call for different things from a person.

`leaguepage/job_recovery.py` answers what a lost job actually did, from
four sources in descending order of proof: the event log, the checkpoints
written either side of each irreversible stage, the publish log file
(which outlives the process), and production itself. It reports and never
re-runs. **Nothing auto-resumes.** `run_job` skips stages the log says
succeeded, so resumption is safe when something does claim a job again,
but no caller does that on its own.

Still process-local, and named here so it is not mistaken for done: the
worker is a daemon thread in the Desk process, so a killed process still
abandons work. The difference is that abandonment is now visible within
ninety seconds instead of invisible forever.

### Multi-device editing

One Commissioner with a laptop and a phone is still concurrency. Every
prose write carries the version it was based on and is refused with a
conflict when that version has moved — this exists now (`base_sha`, 409)
and must survive the repository cutover. Approval binds to a content
signature for the same reason; CTP already works this way.

### Where prose lives

Built 2026-09-05. `leaguepage/prose_store.py` is the contract and the
filesystem backend; `leaguepage/prose_postgres.py` is the Postgres one.

**A key, not a path.** `ProseKey(league, season, issue, kind, name)` names
one editable object. Four kinds:

| kind | what it is | filesystem shape |
| --- | --- | --- |
| `section` | the Lowdown and every module's copy | `lowdown/lowdown.md`, `sections/<key>.md` |
| `matchup` | a week's previews, Commissioner-written by product rule | `matchups/<slug>/draft.md` |
| `proposal` | what Claude Code or ChatGPT handed back | `proposals/<section>.md` |

`key.section_id` is the string the editorial metadata tables have always
used (`matchup:<slug>` included), so `prose_revisions`,
`section_prose_state`, `prose_provenance` and `issue_modules` keep their
existing addresses. Moving prose was not a metadata migration.

**Version vs content hash.** The version is a storage token: it moves when
the stored bytes move, and an edit must be based on one. The content hash
is editorial identity, normalised as provenance has always normalised it,
and is what approval and "changed since published" ask. A trailing newline
is a new version and the same content.

The filesystem backend derives its version from the content because
`editorial/` is a working tree edited outside the Desk; a counter beside
the file could not see those edits and a stale save would destroy one.
Postgres counts, because nothing but the application writes that table.
Callers never interpret either token.

**One authoritative backend, always.** `LEAGUEPAGE_PROSE_BACKEND` selects
it; the default is `filesystem`. There is no dual-write and no fallback: a
Postgres backend that cannot be reached raises rather than writing to disk.
`/health` reports which store is live, with no path, host or DSN in it.

**Cutover tooling**, none of which changes the authoritative backend:

    scripts/prose_tool.py inventory        every key, and its path
    scripts/prose_tool.py export --to DIR  authoritative store -> Markdown
    scripts/prose_tool.py import           filesystem -> postgres (dry run)
    scripts/prose_tool.py verify           compare; nonzero on any mismatch

`verify` prints keys, hashes and versions and never the prose.

### The cutover boundary: what must commit together

Determined 2026-09-06 by reading the code, so it does not depend on
reaching a database. **This is the finding that decided the prose
cutover.**

Prose is one object in one store. Every Commissioner action that touches
prose also writes editorial metadata that is *not* in that store, and the
two are not one transaction. Moving prose alone does not create a hosted
Desk; it creates two databases that can disagree about the same click.

**What one click writes**

| operation | prose | editorial metadata | disk |
| --- | --- | --- | --- |
| **Save** | `put` + a revision | `prose_provenance` (first write only), `section_prose_state`, `issue_modules.approved = 0` **or** `matchup_state.status`, `meta` staleness | — |
| **Accept proposal** | `put` + a revision, then `delete` of a second object | `prose_provenance`, `section_prose_state`, approval, `meta`, `issue_revision_requests` → done | rewrites `REVISION_REQUESTS.md` |
| **Discard proposal** | `delete` | `prose_provenance` assistance, `issue_revision_requests` → withdrawn | rewrites `REVISION_REQUESTS.md` |
| **Restore** | reads a revision, then `put` + a revision | `section_prose_state`, approval, `meta` | — |
| **Reset generated** | `put` + a revision | `prose_provenance`, `section_prose_state = generated`, approval, `meta` | **reads** `rough-lowdown.md` |
| **Replace with my copy** | `put("")` + a revision | `provenance.mark_commissioner`, `section_prose_state`, approval, `meta` | — |
| **Approve / unapprove** | reads it, to refuse an empty or marked section; CTP reads every preview to sign | `issue_modules(approved, approved_sha)` **or** `matchup_state.status`, `meta` cleared | — |
| **Matchup edit** | `put` + a revision | `matchup_state.status → edited`, `meta` for that matchup **and** for `ctp` | — |
| **Request rewrite** | — | `issue_revision_requests` insert | rewrites `REVISION_REQUESTS.md` |

Read it as: every row but the last spans two stores, and three of them
also touch the filesystem.

**The state that must move with prose**

| table | what it holds | in `migrations/`? |
| --- | --- | --- |
| `prose_revisions` | undo history | yes (0001) |
| `section_prose_state` | generated vs commissioner-edited | **no table** |
| `prose_provenance` | the authorship claim | **no table** |
| `issue_modules` | approval, inclusion, order, title | yes, but **without `approved_sha`** |
| `matchup_state` | a preview's approval and angle | yes, but without `revision_requests` |
| `issue_revision_requests` | the rewrite queue | yes (0001) |
| `meta`, `stale:` prefix | changed-since-approval | `editorial_meta` exists; **nothing routes to it** |

Ten further editorial tables (`story_decisions`, `award_decisions`,
`takes`, `force_flow_notes`, `team_names`, `issues`, `bit_usage`,
`editorial_usage`, `power_rankings`, `sync_snapshots`) a hosted Desk
needs but no single prose action writes, so they can move on their own
schedule. `tests/test_schema_parity.py` compares the two schemas
statically and declares every gap with its reason; it needs no database.

**The two deterministic breaks, and where they stand**

1. ~~**History and Restore read SQLite directly.**~~ **Fixed 2026-09-06.**
   Four reads of `prose_revisions` went around `ProseRepository`. Under
   the filesystem backend that was indistinguishable from correct,
   because that backend keeps its revisions in SQLite; under Postgres the
   History panel would have emptied and Restore would have had nothing to
   restore. All four now go through the contract, and
   `revision_counts()` answers for a whole issue in one call instead of
   one connection per card.
2. **Approval is a flag, not a signature.** `issue_modules.approved` and
   `matchup_state.status` are bare values cleared by the save route
   *after* the prose write. Every path through the Desk clears them, so
   the Desk is right; the storage layer is not, because there is no
   shared transaction. A prose write that lands beside a metadata write
   that does not leaves a green approval chip over text nobody approved.
   **This one is unfixed and it is the cutover blocker.**

`prose_provenance` is the model to copy: it stores a hash of the text it
describes, so a write that lands without it claims nothing rather than
claiming something false. Common Tactical Picture already works this way
(`approved_sha`) — and that column does not exist in the Postgres schema,
so today the one correct mechanism is the one that could not migrate.

**Five more of the same class**, all read-and-write coupling rather than
outright breakage:

| # | risk | effect after a cutover |
| --- | --- | --- |
| 1 | `section_prose_state` written after the prose write, and absent from Postgres | the generated/edited chip and `_authority` disagree with the text |
| 2 | `meta` staleness read with a raw `LIKE` through `s._conn` | the changed-since-approval banner silently stops working |
| 3 | accept = `put` target then `delete` proposal, two objects | a crash between them re-shows an accepted proposal |
| 4 | `REVISION_REQUESTS.md` written inside the proposal action | a read-only serverless filesystem fails the whole action |
| 5 | `reset-generated`, `_ai_help_present` and `lowdown_state` read `rough-lowdown.md` from disk | no generated version, no assistance record, wrong workflow status |

### What the live database proved (2026-09-06)

Migrations 0001, 0002, 0004 and 0005 applied; allowlist seeded; every
check below run against the real project.

**RLS, from the roles the application uses.** The DSN connects as
`postgres`, which has `BYPASSRLS`, so a select on it proves nothing and
was never treated as proof. Each probe instead assumed an application
role inside a transaction that was rolled back — `set local role`, with
the JWT claims PostgREST would have set — because RLS is fully in force
for an assumed role.

| caller | read `sections` | insert into `sections` |
| --- | --- | --- |
| `anon` (publishable key) | refused, 42501 | refused, 42501 |
| `authenticated`, not on the allowlist | 0 rows | refused, policy violation |
| `authenticated`, on the allowlist | 34 rows | permitted |

The 34-versus-0 is the discriminating result: the same query, the same
role, a different JWT email. `app_is_commissioner()` returns true only
for the seeded address. The anon half was confirmed independently over
PostgREST with the publishable key, which is the real transport.

**Prose, end to end.**

| step | result |
| --- | --- |
| import dry run | 34 create · 0 replace · 0 identical · 0 conflict · 0 postgres-only |
| import applied | 34 created |
| import re-run | 0 create · 34 identical — idempotent, dry and applied |
| verify | same=34 · filesystem-only=0 · postgres-only=0 · content-differs=0 |
| export to a fresh tree | 34 files, 0 differ, 0 missing either way |
| assemble every issue, Postgres backend | 27/27 module hashes identical to the filesystem run |
| render a preview, Postgres backend | 101 HTML files byte-identical; privacy audit clean |
| workspace publication QA, Postgres backend | identical: 4 issues, 0 blockers, 5 warnings |

The parity runs carry a negative control, because "identical" would also
be what a backend that quietly read the same files would produce: the
Postgres-backed assembly was re-run against a copy of the editorial tree
with **all 34 prose files emptied**. The filesystem backend's output
changed, and the Postgres backend's did not. The words came from the
database.

**One real bug, findable only here.** `ProseConflict` is a sibling of
`ProseError`, not a subclass, and `_tx` re-raised only `ProseError`.
Every optimistic-concurrency refusal on the Postgres backend was
therefore reported as *the backend is unreachable*: the Desk would have
shown a stale save as an outage and never reached the conflict screen.
Four contract tests caught it the moment they could actually run. Fixed.

**What the import did not carry**, measured rather than assumed:

| | Postgres | SQLite |
| --- | --- | --- |
| `sections.state` = `commissioner-edited` | 0 | 28 |
| `prose_revisions` | 0 | 650 |
| `issue_modules` (approvals) | 0 | 50 |
| `matchup_state` | 0 | 13 |
| `issue_revision_requests` | 0 | 1 |
| `issues` | 0 | 4 |
| `takes` | 0 | 3 |

`sections.state` is the sharpest of these. The column **exists** in
Postgres and holds exactly what `section_prose_state` holds in SQLite —
and nothing writes it, because `desk_editor` calls `s.set_prose_state()`
against SQLite while the repository writes `content` and `version`. After
a cutover the Desk would tell him all 28 sections he wrote are generated
drafts.

**And the approval that is done correctly is not in use.** `approved_sha`
is null on both live CTP approvals: they were granted before the
signature existed and are grandfathered. So today no approval anywhere in
the system is actually content-bound — the mechanism exists, the column
to carry it does not exist in Postgres, and no live row exercises it.

**PostgREST's schema cache is stale.** `job_events` and `sync_snapshots`
answer PGRST205 while direct SQL shows both present with RLS forced and a
policy. `NOTIFY pgrst` does not reach PostgREST through the connection
pooler; the remedy is Dashboard → Settings → API → Reload schema cache.
Low severity, because no data flows over PostgREST — `supabase_client`
does authentication only — but the verifier now says so instead of
pointing at the wrong migration.

### The auth residual

Analysed 2026-09-06. Three pieces of per-process state in `auth.py`,
which the roadmap had carried as one item. They are not one item.

| state | class | hosted verdict |
| --- | --- | --- |
| `_EPHEMERAL` (auth.py:83) | hosted-required | **already closed.** With `LEAGUEPAGE_AUTH_MODE=required`, `_secret()` raises rather than minting a per-process key. The path is dead hosted. |
| `_LOGIN_ATTEMPTS` (auth.py:45) | hosted-required | **low, and leave it.** N workers means N × 5 attempts per window and a restart resets the count. What it throttles is a magic-link request and an OTP verify against an allowlist of one address. Supabase applies its own OTP rate limit, which is the one that matters; a shared counter would buy a round trip per attempt and no security. |
| `_USED_LOGIN_JTI` (auth.py:46) | local fallback only | **the only real property, and it was unreachable hosted.** `/auth/callback` is reached only by a token minted at `desk.py`, and that branch runs only when Supabase is *not* configured. Hosted means configured, so hosted mints six-digit OTPs and never a redeemable link. |

What Supabase OTP makes unnecessary remotely: single-use redemption,
expiry and delivery are the provider's, and the allowlist is re-checked
against the address *Supabase returns*, never the posted field.

**Smallest hosted-safe solution — implemented 2026-09-06.** No shared
store. The only property that needed to survive a restart was single-use
redemption, and hosted does not mint redeemable links, so the fix was to
enforce that invariant instead of relying on it: a Desk with
`auth_required()` and no OTP provider **refuses to mint a local magic
link**, logs why, and returns the same reply a stranger gets. One
condition, no infrastructure. If a shared store is ever wanted anyway,
the jti set is the only one worth a table; the rate limiter is not.

### Research stays out, and needs its own answer

Reconfirmed after the migration. These are evidence, not publication state,
and none of them is in `ProseRepository`. A hosted Desk cannot read local
files, so each needs a classification before the cloud tranche:

| artifact | class | why |
| --- | --- | --- |
| `sections/AUTHORING-*.md`, `lowdown/AUTHORING.md` | **B** recomputable | `issue_builder` writes them from the database; a hosted worker can regenerate on demand |
| `lowdown/PREP.md` | **B** recomputable | same builder, same inputs |
| `COMMAND_BRIEF.md`, `REVIEW_PACKET` | **B** recomputable | derived views, rebuilt per issue |
| `matchups/<slug>/generated/**` | **B** recomputable | `matchup_packet` rebuilds from synced data |
| `generated/week.json`, `generated/team_dossiers/**` | **B** recomputable | build artifacts |
| `lowdown/{themes,outline,rough-lowdown}.md` | **C** needs a store | written by a Claude Code session on this machine, read by the Desk. Nothing can recompute them, and a hosted Desk cannot see them |
| `matchups/<slug>/commissioner_notes.md` | **C** needs a store | his own notes; seeded once and then his |
| `REVISION_REQUESTS.md` | **A** local only | a convenience file for a local Claude session; the queue itself is in `issue_revision_requests` |

Only the two **C** rows are real work, and both are "AI drafts and private
notes that arrive from outside the Desk".

#### The two C rows, traced (2026-09-06)

| | `lowdown/{themes,outline,rough-lowdown}.md` | `matchups/<slug>/commissioner_notes.md` |
| --- | --- | --- |
| **written by** | a Claude Code session on this machine, following the brief in `issue_builder` | seeded once by `matchup_packet.build` and never overwritten; after that, only by Jonathan |
| **read by** | the Desk Lowdown screen (all three); `review_packet` (themes as alternates, rough as a status line); `issue_builder.lowdown_state`; `desk_editor._ai_help_present`; `reset-generated`; `_take_candidates` | `matchup_packet._authoring_md` **only** — pasted into that matchup's AUTHORING brief, which is what the next Claude Code run reads |
| **scope** | one issue | one matchup |
| **privacy** | private; named in `privacy.py` and `audit_repo_privacy.py` | private, and stronger: his unfiltered reading of a real person's team. Stripped from all git history on 2026-08-31 |
| **retention** | the life of the issue, and longer: the rough draft is the evidence behind a provenance claim | indefinite, by design |
| **why not recomputable** | a writer's judgment, not a derivation. Nothing in the database knows which three frames were proposed or which he chose | they are his own words |

Two consequences the class-C label alone did not make explicit:

- **`rough-lowdown.md` is not only research.** Its *existence* decides the
  Lowdown's workflow status (`drafting` vs `ready`) and decides whether
  provenance records that AI assistance reached the section. A published
  authorship claim depends on a file a hosted Desk cannot see.
- **`commissioner_notes.md` is an authoring input, not a note to self.**
  It reaches the next draft through the AUTHORING brief. Losing it does
  not lose a note; it silently removes his steer from the writing.

**Smallest durable representation.** Not a research database, and not a
second `ProseRepository`:

    research_artifacts(league_slug, season, issue_key, scope, name,
                       body, updated_at)
      primary key (league_slug, season, issue_key, scope, name)

`scope` is `lowdown` or `matchup:<slug>`; `name` is the filename. Five
named artifacts per issue, one writer and one reader each. Deliberately
not versioned, not searchable, and with no undo: this is evidence, and
giving it undo semantics would mean deciding whose undo it is. The class-B
files stay out — a worker regenerates those.

### Every mutating route, and what it writes

Added 2026-09-07, because the boundary analysis above was built from an
inventory of nine editor routes and **the application registers
forty-four mutating routes**. An inventory covering a fifth of the
surface is not evidence.

Two independent passes, neither trusting the other:

- **Code search.** A static call-graph walk from every registered
  handler, collecting reachable writes: Storage methods whose bodies
  contain INSERT/UPDATE/DELETE, `Path.write_text`/`write_bytes`/
  `unlink`/`mkdir`, and `ProseRepository.put`/`delete`.
- **Route-level acceptance tests.** `tests/test_hosted_mutation_audit.py`
  drives each route with sqlite3's own trace callback installed, so every
  statement reaching a Storage connection is recorded by table — raw SQL
  included — along with the COMMIT count, the files touched inside the
  editorial tree, and every repository write. Each route's observed
  writes are compared against a written-down claim, and a guard test
  fails if any registered mutating route is undeclared.

**Safe for hosted execution** means both of: the route performs no
authoritative write to this machine, and everything it writes commits
together.

Almost nothing satisfies the second, and the reason is structural rather
than incidental: **`Storage._cursor()` commits after every mutating
method**, so a route calling three Storage methods is three
transactions. On one machine the next request repairs the difference.
Once the two halves of a click live in two databases, nothing does.

#### Authoring — the weekly loop (35 routes)

| route / action | cloud writes | local authoritative writes | transaction owner | safe hosted |
| --- | --- | --- | --- | --- |
| `add_take`<br><sub>/commissioner/{lg}/{yr}/draft-review/take</sub> | — | `takes` | none | NO |
| `award_decide`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/awards</sub> | — | `award_decisions` | none | NO |
| `decide_award`<br><sub>/commissioner/{lg}/{yr}/draft-review/award</sub> | — | `award_decisions` | none | NO |
| `decide_story`<br><sub>/commissioner/{lg}/{yr}/draft-review/story</sub> | — | `story_decisions` | none | NO |
| `editor_approve`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/approve</sub> | — | `issue_modules`, `matchup_state`, `meta` | none | NO |
| `editor_custom`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/custom</sub> | — | `issue_modules` | none | NO |
| `editor_module`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/module</sub> | — | `issue_modules` | none | NO |
| `editor_rankings`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/rankings</sub> | — | `power_rankings` | none | NO |
| `editor_replace_origin`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/replace-origin</sub> | `sections`, `prose_revisions` | `prose_provenance`, `section_prose_state`, `issue_modules`, `matchup_state`, `meta` | none | NO |
| `editor_reset_generated`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/reset-generated</sub> | `sections`, `prose_revisions` | `prose_provenance`, `section_prose_state`, `issue_modules`, `matchup_state`, `meta` | none | NO |
| `editor_restore`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/restore</sub> | `sections`, `prose_revisions` | `section_prose_state`, `issue_modules`, `matchup_state`, `meta` | none | NO |
| `editor_save`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/save</sub> | `sections`, `prose_revisions` | `prose_provenance`, `section_prose_state`, `issue_modules`, `matchup_state`, `meta` | none | NO |
| `false_assumption_decide`<br><sub>/commissioner/{lg}/{yr}/false-assumptions/{take_id}</sub> | — | `takes`, `story_decisions` | none | NO |
| `force_flow_note`<br><sub>/commissioner/{lg}/{yr}/force-flow/note</sub> | — | `force_flow_notes` | none | NO |
| `inbox_decide`<br><sub>/commissioner/{lg}/{yr}/inbox/decide</sub> | — | `story_decisions` | none | NO |
| `inbox_reviewed`<br><sub>/commissioner/{lg}/{yr}/inbox/reviewed</sub> | — | `sync_snapshots` | none | NO |
| `issue_module_update`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/builder/module</sub> | — | `issue_modules` | none | NO |
| `lowdown_save`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/lowdown</sub> | `sections`, `prose_revisions` | `section_prose_state` | none | NO |
| `matchup_angle`<br><sub>/commissioner/{lg}/{yr}/week/{week}/matchups/{slug}/angle</sub> | — | `matchup_state`, `meta` | none | NO |
| `matchup_draft_save`<br><sub>/commissioner/{lg}/{yr}/week/{week}/matchups/{slug}/draft</sub> | `sections`, `prose_revisions` | `matchup_state`, `meta` | none | NO |
| `matchup_prominence`<br><sub>/commissioner/{lg}/{yr}/week/{week}/matchups/{slug}/prominence</sub> | — | `matchup_state` | none | NO |
| `matchup_revision`<br><sub>/commissioner/{lg}/{yr}/week/{week}/matchups/{slug}/revision</sub> | — | `matchup_state` | none | NO |
| `matchup_status_change`<br><sub>/commissioner/{lg}/{yr}/week/{week}/matchups/{slug}/status</sub> | — | `matchup_state`, `meta` | none | NO |
| `proposal_action`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/proposal</sub> | `sections`, `prose_revisions` | `prose_provenance`, `section_prose_state`, `issue_modules`, `matchup_state`, `meta`, `issue_revision_requests` | none | NO |
| `qa_action` °<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/qa-action</sub> | — | `meta`, `issue_modules` | none | NO |
| `rankings_save`<br><sub>/commissioner/{lg}/{yr}/rankings/{label}</sub> | — | `power_rankings` | none | NO |
| `request_rewrite`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/request-rewrite</sub> | — | `issue_revision_requests` | none | NO |
| `resolve_take`<br><sub>/commissioner/{lg}/{yr}/draft-review/take/{take_id}/resolve</sub> | — | `takes` | none | NO |
| `save_power`<br><sub>/commissioner/{lg}/{yr}/draft-review/power</sub> | — | `power_rankings` | none | NO |
| `set_team_names`<br><sub>/commissioner/{lg}/{yr}/team-names</sub> | — | `team_names` | none | NO |
| `set_theme`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/theme</sub> | — | `issues` | none | NO |
| `story_decide`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/stories</sub> | — | `story_decisions` | none | NO |
| `take_action`<br><sub>/commissioner/{lg}/{yr}/take/{take_id}</sub> | — | `takes` | none | NO |
| `track_take`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/take</sub> | — | `takes` | none | NO |
| `use_sleeper_name`<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/use-sleeper-name</sub> | — | `team_names` | none | NO |

#### Operational (4 routes)

| route / action | cloud writes | local authoritative writes | transaction owner | safe hosted |
| --- | --- | --- | --- | --- |
| `about_preview` °<br><sub>/commissioner/site/about/preview</sub> | — | — | n/a | **YES** |
| `about_save` °<br><sub>/commissioner/site/about</sub> | — | `editorial/site/about.md` | none | NO |
| `issue_build` °<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/build</sub> | — | — | n/a | NO |
| `sync_start` °<br><sub>/commissioner/sync-start</sub> | — | — | job | NO |

#### Publication (2 routes)

| route / action | cloud writes | local authoritative writes | transaction owner | safe hosted |
| --- | --- | --- | --- | --- |
| `issue_publish` °<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/publish</sub> | — | `published/**.json`, `issues` | none | NO |
| `publish_start` °<br><sub>/commissioner/{lg}/{yr}/issue/{issue}/edit/publish-start</sub> | — | — | job | NO |

#### Authentication (3 routes)

| route / action | cloud writes | local authoritative writes | transaction owner | safe hosted |
| --- | --- | --- | --- | --- |
| `auth_logout` °<br><sub>/auth/logout</sub> | — | — | n/a | **YES** |
| `auth_request` °<br><sub>/auth/request</sub> | — | — | n/a | **YES** |
| `auth_verify` °<br><sub>/auth/verify</sub> | — | — | n/a | **YES** |

**44 mutating routes · 4 safe · 35 of 35 authoring routes unsafe.** `°` marks a route declared but not driven by an acceptance test.

The four YES rows are the three authentication routes, whose durable
state is a cookie, and the About preview, which renders to a temporary
path. **No authoring route is safe**, so the cutover gate is closed —
and it is closed in a test
(`test_no_authoring_route_is_safe_for_hosted_execution_yet`) rather than
in a paragraph, so it fails the day that stops being true.

### Killing the process between the two halves of a click

`tests/test_crash_between_coupled_writes.py`. A green happy path cannot
see a seam, because on the happy path both writes land. Each test arms a
fault at one Storage method, drives the route, gets a 500, then discards
the app and builds a new one against the same database and editorial
tree — the Desk keeps no state between requests except its job rows, so
that is a faithful restart. Every assertion is made on the restarted
Desk. The fault disarms on restart, because a process that died once is
replaced by a working one, not by a permanently broken build.

| seam | what the restarted Desk shows |
| --- | --- |
| **save**, die before `set_prose_state` | the new text is stored **and the approval survived it**. A green approved chip over text nobody approved, with no signature that could detect it |
| **restore**, same seam | the restored text is stored and the approval outlives it again |
| **replace with my copy**, die before the authorship rewrite | the section is empty and provenance still says a machine wrote it |
| **accept proposal**, die between the two prose objects | the accepted text is in place **and the proposal is still offered**, because deleting it was a separate step |
| **request rewrite**, die between the row and the file | the database has both requests and `REVISION_REQUESTS.md` — the file a local Claude Code session reads — has one |
| **provenance**, die before it is written | **nothing is claimed.** Origin reads `unknown`, which is honest |
| a **refused** save (409) | nothing at all; optimistic concurrency never reaches the seam |

The sixth row is the one to copy. Provenance stores a hash of the text it
describes, so a half-completed click leaves it silent rather than wrong.
Common Tactical Picture's `approved_sha` works the same way, and a test
here shows its signature moving when a preview is edited. The fix for the
first four rows is that mechanism, not a distributed transaction.

### The local semantic contract (Tranche 5A, 2026-09-07)

The route audit found forty-four mutating routes and one structural
defect underneath all of them: `Storage._cursor()` committed after every
mutating method, so a Commissioner click was several committed
transactions that happened to share a file. 5A fixes the local semantics
first, on the machine where they are observable, and the result is the
contract the Postgres backend has to implement exactly.

**One click, one transaction.** `Storage.transaction()` is a depth-counted
scope. Inside it, mutating methods stop committing on their own and the
outermost scope commits once or rolls the whole thing back. Outside it
nothing changed, so no existing caller had to learn about it. Nesting is
by depth rather than SAVEPOINT because the property that matters is that
an inner scope cannot commit the outer one's work, and nothing needs an
inner rollback. A failure anywhere poisons the scope: a body that
swallows its own exception gets `TransactionAborted` and a rollback,
because committing half a Commissioner action silently is the thing this
exists to prevent.

**SQLite cannot commit a filesystem write, and this does not pretend
otherwise.** Prose is still a file. What closes the gap is not a
distributed transaction but the second half of the contract.

**Every claim about prose carries the identity of the prose.** Approval
was a boolean that a later write had to clear; a crash between the prose
write and that clear left a green chip over text nobody approved, which
the fault injection demonstrated. Now `module_signature()` gives every
approvable module one canonical signature — Common Tactical Picture's
composite, the saved entries for Power Rankings, the prose for everything
else — and `module_approved()` means *he approved it AND what is there
now is what he approved*. Nothing has to notice an edit. Nothing has to
clear a flag. Putting the exact text back makes the approval valid again,
because a signature is a comparison rather than a latch.

That deleted more than it added: `_invalidate_approval`, `_mark_changed`,
`_stale_key` and `_stale_sections` are all gone, and with them the
`approval-stale:` rows in `meta` and the raw `LIKE` scan through
`s._conn` that read them. "Changed since approval" is now the signature
failing to match, which cannot be out of step with the text.

**Legacy approvals.** An approval with no signature predates signatures.
We know he clicked; we do not know what it said. For an issue that is
still editable that is not evidence about the current text, so it reads
as not-currently-approved and he re-approves. Published snapshots are
immutable files and never consult this, so history is untouched. The two
live CTP approvals are in exactly this state.

**A behaviour change worth knowing about:** a correction now requires
re-approval. Under the old boolean a correction could republish changed
words under the approval that covered the words it replaced — the same
failure as the crash case, arriving through a different door.

**What still cannot be made atomic locally, and is honest about it.**
Accepting a proposal is two prose objects: put the target, delete the
proposal. No SQLite transaction joins two filesystem writes, so a crash
between them leaves the accepted text in place and the proposal file
beside it. Rather than delete the evidence to hide the ambiguity, the
Desk recognises the state: a proposal whose text is already the section's
text is not a proposal, and the card says *already accepted* with a
button that only clears the file. Retirement is idempotent.

**`REVISION_REQUESTS.md` is derived.** `issue_revision_requests` in
SQLite is authoritative for what has been asked for; the file exists so a
local Claude Code session can read the queue without the Desk running. It
is regenerated from the rows on every change and again whenever the Issue
Room loads, so a crash leaves the file stale and the database right, and
the next page load repairs it. File content never determines request
truth.

#### Filesystem writes, classified

| write | class |
| --- | --- |
| prose under `editorial/**` (repository, filesystem backend) | **AUTHORITATIVE TODAY** |
| `editorial/site/about.md` | **AUTHORITATIVE TODAY** — the only editable prose outside the repository |
| `lowdown/{themes,outline,rough-lowdown}.md` | **AUTHORITATIVE TODAY** — research, and nothing can recompute it |
| `matchups/<slug>/commissioner_notes.md` | **AUTHORITATIVE TODAY** — his own words |
| `REVISION_REQUESTS.md` | **DERIVED / REGENERABLE** as of 5A |
| `AUTHORING*.md`, `PREP.md`, `COMMAND_BRIEF.md`, `REVIEW_PACKET`, `matchups/*/generated/**`, `generated/**` | **RECOMPUTABLE RESEARCH** |
| `published/**.json` | **PUBLICATION ARTIFACT** — immutable |
| `dist/**` | **DERIVED / REGENERABLE** |
| `logs/login-links.log`, `backups/**`, `prose_tool export` output | **BACKUP / EXPORT** |

#### Transaction ownership, after 5A

`jobs.py` opens its own connection with `isolation_level=None` and never
goes through `Storage`, so the durable job control plane is untouched by
any of this — which is correct: an operational job is not part of a
Commissioner click and has its own transactional requirements. The only
other direct commits are `Storage.__init__` creating the schema and the
two migration scripts, each on its own connection. There were no legacy
helper commits to remove.

**Locally crash-consistent is not hosted-safe.** After 5A a process can
die between any two internal steps of a Commissioner click and the
application never afterwards presents metadata that falsely describes the
prose that survived — metadata may be missing, which is honest, or
visibly stale, but not wrong. Every authoring route but *accept proposal*
is in that state. **None of them became hosted-safe**, because nothing
about a local transaction removes a local filesystem write. The route
table carries both columns separately for exactly this reason.

---

## Next tranche — UNIFIED CLOUD EDITORIAL STATE

Specified 2026-09-06, after the live validation. The prose half is done
and proved; this is the half that makes a cutover safe. Nothing here
requires new product thinking, which is why it can be specified exactly.

**The goal, stated as an invariant.** One Commissioner action commits or
does not commit. Today a save writes prose to one store and four pieces
of metadata to another in four further transactions, and a fault
injected between them leaves a green approval chip over text nobody
approved — demonstrated, not predicted.

**Scope, from the route audit: 35 authoring routes, not 9.** The nine
editor routes are the hardest, because they are the ones that write both
stores. The other 26 each write a single SQLite table with no Postgres
path, which is simpler work but is still work, and none of it can be
skipped: a hosted Desk that cannot record an award decision is not a
hosted Desk.

### 0. ~~Make one Commissioner action one transaction~~ — done in 5A

### 1. ~~Make approval content-bound~~ — done in 5A

Done on SQLite in Tranche 5A, where it was observable. What follows is
the record of what was built, because 5B has to implement exactly this in
Postgres.

- `issue_modules.approved_sha` already exists in SQLite and is already
  the right mechanism; extend it from `ctp` to every module kind. The
  signature is the section's normalised content hash, which is what
  `provenance` already computes.
- `matchup_state` gains the same: a preview's approval is a signature
  over its own text, not the enum `approved`.
- `_invalidate_approval` stops being a write. An approval whose signature
  no longer matches the stored text is simply not an approval — the same
  reasoning provenance uses, and one mechanism rather than two that can
  disagree. This deletes code.
- Grandfather explicitly: a row with `approved = true` and a null
  signature keeps counting, exactly as the two live CTP rows do now.
  Write the test that pins that, because both live approvals are in that
  state today.

Done when: editing a section retires its approval with no code noticing,
and `test_ordinary_approval_is_a_flag_and_not_a_signature` fails and is
replaced.

### 2. Close the schema gaps

`tests/test_schema_parity.py` declares every one of them and will tell you
when the list is empty. As a new migration, `0006_editorial_state.sql`:

- `alter table issue_modules add column approved_sha text`
- `alter table issues add column theme text`
- `alter table matchup_state add column revision_requests jsonb`
- `create table prose_provenance` — mirror of the SQLite shape, including
  `origin`, `assistance`, `baseline_text`, `event`
- `create table force_flow_notes`
- **not** `section_prose_state`: Postgres already has `sections.state`,
  and the fix is a caller, not a table. Give `ProseRepository` the state
  and let the backend decide where it lives.
- RLS enabled *and* forced on each new table, one `commissioner_all`
  policy, `revoke all from anon` — copy the `do $$` block from 0001 so
  nothing is locked down by hand.
- `revoke all on function app_is_commissioner() from public`. The
  existing `revoke ... from anon` does not remove PUBLIC's default
  EXECUTE grant, so anon can still call it. It only ever returns a
  boolean about the caller, so nothing leaks, but the migration does not
  currently do what it says.

### 3. Give the repository the rest of the click

The metadata a prose write must carry moves into the contract, so the
backend decides the transaction rather than the caller:

    put(key, text, *, expected_version, source, state=None,
        provenance=None, invalidates_approval=True) -> Prose

- Filesystem backend: the existing SQLite writes, unchanged in effect.
- Postgres backend: one transaction — `sections` (content, version,
  state), `prose_revisions`, `prose_provenance`, and the approval
  signature check, committed together or not at all.
- `_changed_since_approval` disappears. It reads `meta` with a raw `LIKE`
  through `s._conn`, and once approval is a signature the question
  "changed since approval?" is answered by comparing two hashes.

### 4. Move the remaining editorial tables

`issue_modules`, `matchup_state`, `issues`, `issue_revision_requests`,
`takes`, `story_decisions`, `award_decisions`, `power_rankings`,
`team_names`, `bit_usage`, `editorial_usage`, `sync_snapshots`,
`force_flow_notes`. `scripts/export_commissioner_state.py` and its import
counterpart already exist; extend rather than replace them, and give each
an idempotence proof the way `prose_tool import` has one.

### 5. Then, and only then, the cutover

The evidence to require, all of which now has a working harness:

- every row of the route table reading YES, which is asserted by
  `test_no_authoring_route_is_safe_for_hosted_execution_yet` failing
- every seam in `test_crash_between_coupled_writes.py` re-asserted to the
  opposite outcome: the restarted Desk shows either both halves or
  neither
- `prose_tool verify` clean, and an equivalent for the metadata tables
- assembly parity 27/27 with the blank-tree negative control
- preview parity byte-identical
- workspace QA identical
- History, Restore, approval and the changed-since-approval banner
  exercised through the Desk against Postgres
- the nine published snapshot hashes unchanged

### Explicitly not in this tranche

The research store, the hosted Vercel project, GitHub Actions
publication, and any change to who may publish. Publication stays local
and stays the Commissioner's.

---

### Portability seams

The future product is one Commissioner with one or more leagues, described
by configuration rather than by code branching on a slug. What already is
configuration: Sleeper league id, public slug, display name, subtitle,
theme key, author roster. What is not, and should become so before a second
Commissioner exists: enabled modules and their order (a per-slug tuple in
`issue_builder.MODULE_DEFS`), logos and colors (hardcoded in
`site_build.OG_IMAGES` and `public/base.html`), archive scope and editorial
frame packs (per-slug dicts in `story_memory` and `matchup_angles`), the
production URL (hardcoded twice), and per-league season.

Do not build billing, signup, a theme editor, or onboarding. Create the
seams while touching the code, and stop there.

### AI providers

A Claude Max or ChatGPT Plus subscription is a person's account, not an
API this application may call. The boundary is `leaguepage/writing_packet.py`:
one structured brief per section, four delivery modes, none of which
change the facts.

- **Manual handoff** (`copy-for-claude`, `copy-for-chatgpt`) — first-class
  and permanent. The packet is copied, the answer returns as a proposal,
  provenance records the provider when it is known.
- **Local worker** (`local-worker`) — the portal queues a writing job; a
  Claude Code process on this machine picks it up when the machine is on
  and writes a proposal back. Never a direct write to prose. Hosted
  authoring must not depend on it.
- **API** (`api`) — per-user key, explicit spend controls, proposals only.
  Not built, and not required by anything above it.

---

## Manual gates — only Jonathan can do these

0. **Apply the pending migrations.** Verified live 2026-09-06 from the
   anon path: `0001` and `0003` are applied; **`0002_change_inbox.sql` and
   `0004_durable_jobs.sql` are not** (`change_inbox`, `sync_snapshots` and
   `job_events` answer PGRST205), and `0005_prose_keys.sql` cannot be
   confirmed either way from anon because it alters a column list. Paste
   each into the SQL Editor as the database owner, in order; 0005 guards
   itself and is safe to re-run.
1. **Seed `app_commissioners`.** Run
   `.venv/Scripts/python.exe scripts/make_commissioner_seed.py`, then run the
   emitted SQL in the Supabase dashboard's SQL Editor as the database owner.
   RLS is forced on that table and its policy requires membership, so the
   application cannot insert its own first row — verified 2026-08-31 and
   again 2026-09-06: anon gets 42501 on every table that exists. The email
   must match `LEAGUEPAGE_COMMISSIONER_EMAILS`. **Everything hosted is
   blocked on this.**
1b. **Put `DATABASE_URL` in `.env`** if the Postgres backend is to be
   proved. It is unset today, and the Postgres repository connects by DSN
   and refuses to fall back, so without it the thirteen Postgres contract
   tests, `prose_tool import` and `prose_tool verify` cannot run at all.
   It is used by migration tooling only — the application talks to
   Supabase over PostgREST with the signed-in Commissioner's token — and
   it never leaves `.env`. Note that a DSN connects as the owner and
   therefore **bypasses RLS**: it can prove the repository contract, and
   it can never prove an authorization rule.
2. **Create the private Vercel project** for the Desk and set its
   environment variables (`LEAGUEPAGE_AUTH_MODE=required`,
   `LEAGUEPAGE_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   database URL). It is a different project from the public site.
3. **`npx vercel login`** in his own terminal if the Desk is to deploy
   directly. A terminal inside a Claude session is not a valid test of
   this: those sessions see a private copy of the credential file.
4. **Approve the morning sync** as a Windows scheduled task, if the Force
   Flow loop is wanted. Machine-level change.
