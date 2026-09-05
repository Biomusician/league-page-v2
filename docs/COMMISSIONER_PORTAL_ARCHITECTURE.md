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
| Prose | `editorial/**/*.md` on disk |
| Editorial state | SQLite: decisions, approvals, provenance, prose revisions, takes, notes |
| Jobs | Daemon threads plus module globals in `publish_jobs.py` and `sync_jobs.py` |
| Publication | Immutable JSON snapshots in `published/`, corrections as sibling revisions |
| Build | Local `build_public_site.py` into `dist/`, privacy audit, then the `site` branch or the Vercel CLI |

The parts worth keeping are the invariants, not the plumbing: immutable
snapshots, correction-not-overwrite, the privacy audit as a build gate,
approval bound to content, provenance recorded rather than inferred.

### What blocks a hosted deployment today

1. **Prose is filesystem state.** Roughly 24 write sites under `leaguepage/`
   put editorial state on disk. A read-only serverless runtime rejects all
   of them.
2. **Jobs are process globals.** `_JOBS`, `_ACTIVE`, `_JOB` and the auth
   rate-limit dictionaries live in one process. On a platform that can end
   a process between requests, a publish loses its progress and its
   single-flight guard, and login rate limiting resets.
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

### Multi-device editing

One Commissioner with a laptop and a phone is still concurrency. Every
prose write carries the version it was based on and is refused with a
conflict when that version has moved — this exists now (`base_sha`, 409)
and must survive the repository cutover. Approval binds to a content
signature for the same reason; CTP already works this way.

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
