# Decisions

Rationale matters more than the decision. Newest at the bottom.

## 2026-08-29 — Static publish, no public server

Published issues are immutable by design (spec §31), so the public site is rendered
to static HTML at publish time. Consequences: no hosting cost beyond static pages,
no public auth, and the Commissioner's Desk runs purely on localhost — its "auth"
is that it never leaves this machine. Chosen over a hosted FastAPI app because the
machine/stack budget (no Node, no Docker) and the immutability requirement made a
live server pure overhead.

## 2026-08-29 — Stack: FastAPI + Jinja2 + htmx + SQLite

Python-only, matching the machine (three Pythons, no Node) and Jonathan's
boring-flat-readable preference. htmx is vendored as a single static file and gives
the desk its interactivity (regenerate paragraph, lock paragraph, approve) without
writing JavaScript. One SQLite DB for everything, mirroring Fantasy Bot's proven
raw-JSON-blob + indexed-columns pattern.

## 2026-08-29 — New repo, code copied (not shared) from Fantasy Bot

Fantasy Bot is decision-support for Jonathan's own teams; League Page is a
publication system for two leagues. The Sleeper client/storage/sync layers were
copied and adapted rather than imported so the two products can evolve
independently. Deliberate duplication over coupling.

## 2026-08-29 — Editorial identity in git JSON; mutable editorial state in SQLite

Manager identities, coalitions, recurring/retired bits, and sensitivity flags live
in `editorial/*.json` — git-tracked, diffable, survives any DB rebuild, editable by
hand. Things that accumulate at runtime (bit usage log, takes/receipts, later issue
workflow state) live in SQLite. Split chosen so wiping/rebuilding the DB never
destroys hand-curated lore.

## 2026-08-29 — Archive imported from Google Drive as git-tracked markdown

The historical newsletters (~40 Google Docs, 2019–2025) are exported to
`archive/<league>/*.md` with small frontmatter headers and checked into git; SQLite
only indexes them (FTS5) for search. The corpus is the long-term asset; the index
is disposable.

## 2026-08-29 — Claude generation via Anthropic API, key in .env — SUPERSEDED

Superseded same day by the entry below; kept for the record.

## 2026-08-29 — Claude Code is the editorial AI; no LLM API dependency in V1

Jonathan corrected the architecture: the V1 pipeline is Sleeper/archive/metadata →
deterministic analytics → structured editorial context → **Claude Code**
authoring/editing → git-tracked published issue. The deployed site must not need an
LLM API to function, and creating a weekly issue must not require separate API
billing. Consequences: no `.env`/API-key requirement anywhere, the `anthropic`
dependency was removed, and the core AI integration is
`scripts/build_editorial_packet.py`, which emits a self-contained authoring context
directory for a Claude Code session. Generated prose is never auto-published. A
one-click in-app API authoring feature remains possible but is explicitly post-V1.

## 2026-08-29 — Verification statuses on editorial metadata

Manager aliases and coalition/roster mappings carry an explicit status: confirmed
facts live in `aliases` / mappings marked `confirmed`; inferences live in
`unverified_aliases` / mappings marked `inferred` with their evidence; rejected
inferences are kept (marked `rejected`) so they aren't re-inferred later. Generated
copy and editorial packets use confirmed material only. Chosen after the
manager-alias inference (a Sleeper handle plausibly matched to an archive
nickname) showed how easily a plausible guess could leak into published prose
as fact.

## 2026-08-29 — Plain HTML forms for the Desk, htmx deferred

The Commissioner's Desk V1 uses server-rendered pages with plain POST forms — no
JS at all. htmx (vendored) becomes worth it when Matchup Lab needs per-paragraph
regeneration UX; adding it before then is speculative.

## 2026-08-29 — FantasyPros ECR as the "ADP" reference source

True ADP isn't freely snapshot-able, but Fantasy Bot's cache already scrapes
FantasyPros expert-consensus ranks (ECR) in the right formats (half-PPR for 1QB
Surfeit, superflex for Disco), refreshed 2026-08-29. Imported as documented
snapshots under `data/adp/` with source name, retrieval date, and format recorded;
every delta the app shows names the dataset ("FantasyPros ECR", not "ADP"). The
importer is an abstraction — any CSV/JSON source with the documented shape can
replace or sit beside it. Missing players yield "no reference rank", never a
fabricated value.

## 2026-08-30 - Private GitHub hosting; Vercel deploys the site branch, never the source

Source lives in PRIVATE repo Biomusician/league-page-v2 (the 2022 public fork
Biomusician/league-page is unrelated and preserved). Vercel's Git integration
would serve the repository root as static files, and the public build cannot
run in CI anyway (it reads the private local SQLite DB), so the deployment
unit stays the locally built, privacy-audited dist/: scripts/push_site_branch.py
syncs it to the "site" branch, which is the Vercel production branch. A
vercel.json on both branches sets git.deploymentEnabled.main=false so a main
push can never deploy. Chosen over committing public-safe build inputs for CI
because that refactor would enlarge the privacy surface for zero reader-visible
gain.

Before the first main push, history (23 commits) was purged with
git-filter-repo: commissioner_notes.md and PREP.md removed from all commits,
two handle strings replaced in old blobs. Rationale: the standing rule is that
real Sleeper handles never enter git at all, and "the repo is private" is one
compromised account away from not mattering. Commissioner notes are local-only
files now, like managers.json. Pre-rewrite history: local bundle
League-Page-pre-github-history-2026-08-30.bundle (gitignored) plus the
2026-08-29 private bundle. scripts/audit_repo_privacy.py gates future main
pushes; the site branch is audited by site_build.audit_output at build time
instead (it knows the verbatim-archive and public-team-name exemptions).

## 2026-08-31 - Remote authoring: cloud-native (Option A), not a Cloudflare bridge

Recon settled this rather than taste. Three measurements decided it.

1. The migration payload is tiny. Authoritative Commissioner state is 247
   database rows + 14 meta keys + 38 prose files (57 KB) - 1.9% of the
   database. The other 12,749 rows (players, rosters, matchups, drafts,
   transactions, archive FTS) are Sleeper/archive cache that any sync
   rebuilds from scratch. Migrating "the database" is not the job;
   migrating a few hundred rows and 57 KB of prose is.
2. Compute already fits a serverless request budget. The heaviest read
   path (positional_profile) is 0.05s, a ghost brief 0.04s, a full sync
   2.6s, a full public build ~1.5s. Vercel Hobby allows 10s per request.
   Nothing here needs a long-running process except by current habit.
3. The real obstacle is not compute or data volume - it is that CURRENT
   PROSE IS FILESYSTEM-AUTHORITATIVE (editorial/**/*.md), and job state
   lives in process memory (daemon threads in sync_jobs/publish_jobs).
   Both die on a serverless runtime. Those are the only two structural
   blockers, and both are contained.

Option B (Cloudflare Tunnel + Access over the localhost Desk) was
rejected as the target architecture because it fails the stated primary
goal: it requires the Windows desktop to be powered on and the Desk
running. That is the dependency the tranche exists to remove. It remains
a legitimate emergency stopgap and is documented as such, not built.

Chosen target: private FastAPI app on its own Vercel project + Supabase
Postgres + magic-link auth + Resend inbound email, with the localhost
Desk retained as fallback against the SAME cloud store (never a second
independent store that can diverge).

Sequencing, deliberately: security and recoverability first (done -
verified export/restore, allowlisted magic-link auth, a middleware that
closes every route by default with one explicit public list and a test
that enumerates the live app to prove it). Then the two structural
blockers (prose repository, durable jobs table). Then email proposals.
Remote publishing stays last and stays optional; local Publish & Deploy
is proven and must not be weakened to gain convenience.

Cost expectation: Supabase free tier, Resend free tier, a second Vercel
project on the existing account. A few hundred rows and 57 KB of prose
will not approach any free-tier limit for years.

Hard constraint discovered: every remaining path requires an external
account (Supabase, Resend, or Cloudflare). Claude cannot create accounts,
so those steps are Jonathan's, and the work was scoped to leave exactly
that and nothing else.

## 2026-08-31 - Supabase email OTP over magic link; server-side exchange

Magic links need Redirect URLs registered in the Supabase dashboard, which
means authentication could not be tested until a hosted URL existed, and
they add redirect-handling surface. A six-digit email OTP needs no
dashboard configuration, so sign-in became testable locally the same day
and the eventual private Vercel URL can be added without touching code.

The OTP exchange runs server-side rather than through Supabase browser
SDK. Consequence: no Supabase credential of any class is ever handed to
the browser, not even the publishable key that Supabase designs to be
browser-safe. The browser posts an email and a six-digit code and receives
our own signed session cookie; a regression test asserts no key appears in
rendered HTML.

Authorization is deliberately kept out of Supabase. Supabase answers who
someone is; the Commissioner allowlist answers whether they may use this
application, and it is consulted independently before an OTP is sent and
again after it is verified, against the address Supabase returned rather
than the one the form posted. Row Level Security repeats the same rule at
the database with a forced policy over an app_commissioners table, so the
answer does not depend on the application being correct.

Also changed: the pending email address moved out of redirect URLs into a
signed short-lived HttpOnly cookie. URLs end up in browser history and
server access logs, which is the wrong home for a personal address, and
the move incidentally made the allowed and rejected sign-in responses
byte-identical.
