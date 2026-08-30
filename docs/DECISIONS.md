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
