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
   durable and leased (see *Durable jobs*, below). What remains of this
   blocker is smaller and separate: the auth rate-limit dictionaries
   (`auth._LOGIN_ATTEMPTS`, `_USED_LOGIN_JTI`, `_EPHEMERAL`) are still
   per-process, so login throttling resets on a restart and a one-time
   login token could be replayed against a second instance. Those belong
   with identity, not with jobs.
3. **The build reads the private database.** `dist/` is produced from
   SQLite and `editorial/`, which is why Vercel never rebuilds and only
   ever receives an audited artifact.
4. **`app_commissioners` is empty.** RLS is forced on that table and its
   policy requires membership, so the app cannot seed its own allowlist.

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
notes that arrive from outside the Desk". That is the shape of the next
research-store decision; it is not solved here.

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

1. **Seed `app_commissioners`.** Run
   `.venv/Scripts/python.exe scripts/make_commissioner_seed.py`, then run the
   emitted SQL in the Supabase dashboard's SQL Editor as the database owner.
   RLS is forced on that table and its policy requires membership, so the
   application cannot insert its own first row — verified 2026-08-31: the
   publishable key gets 401. The email must match
   `LEAGUEPAGE_COMMISSIONER_EMAILS`. **Everything hosted is blocked on this.**
2. **Create the private Vercel project** for the Desk and set its
   environment variables (`LEAGUEPAGE_AUTH_MODE=required`,
   `LEAGUEPAGE_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   database URL). It is a different project from the public site.
3. **`npx vercel login`** in his own terminal if the Desk is to deploy
   directly. A terminal inside a Claude session is not a valid test of
   this: those sessions see a private copy of the credential file.
4. **Approve the morning sync** as a Windows scheduled task, if the Force
   Flow loop is wanted. Machine-level change.
