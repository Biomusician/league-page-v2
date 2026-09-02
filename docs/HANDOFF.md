# HANDOFF

Updated 2026-08-29, end of the MVP-to-Vercel tranche. Companions:
docs/SPEC.md (product spec), docs/DECISIONS.md, docs/DEPLOY.md (deploy
playbook), **docs/ROADMAP.md (ranked future work)**, POST_MVP.md (backlog).

This file is IMPLEMENTATION STATE. Future features belong in ROADMAP.md.

## Tier 1 shipped: the weekly triage loop (2026-09-01)

**Status: built, tested, exercised against a copy of the live database. Not
deployed anywhere public (it is Desk-only, and the Desk is localhost).**

Sync now records a snapshot, the Change Inbox diffs it, and one shared
significance model ranks everything with its reasoning attached.

- `leaguepage/significance.py` — the shared interpretable scorer. Named
  components each carrying points and evidence, clamped 0..100, same idiom as
  `matchup_interest`. Two NEGATIVE components (repetition, triviality) do the
  real work: without them a long tail of true-but-boring facts outranks a
  genuine upset by accumulating small positives.
- `leaguepage/change_inbox.py` — `capture_state` / `record` per sync, then
  `diff_snapshots` + `transaction_items` + `receipt_items` merged with the
  existing `weekly_story_candidates` and ranked. Every item carries BEFORE and
  AFTER, not just a headline.
- `sync_snapshots` table (migration `0002_change_inbox.sql`). One row per
  MATERIAL sync: an identical payload is not stored, so pressing Sync twice
  does not blank the inbox. `snapshot_id`, not `taken_at`, is the ordering key
  because two syncs in the same second are ordinary.
- **No new decision store.** Add to Issue / Ignore This Week / Save for Later
  are `story_decisions`' existing include/ignore/save plus `route`, so an inbox
  decision is the same row the issue builder and authoring briefs already read.
- `/commissioner/inbox` plus a WHAT CHANGED? button on Desk home. Mobile-first
  (thumb-sized actions, no horizontal tables, its own media query).
- **Postgame auto-refresh**: `matchup_packet.phase_of` flips a brief from
  preview to result once real points exist, and the result block forbids
  claiming causation the data does not support. `refresh_issue_research`
  already ran on every sync and already preserved prose; it now also gets
  timed. There is no separate recap workflow, by design.
- Sync and inbox timings are recorded on the sync job (`_timing`) and shown
  under the inbox, so "do not make Sync feel slow" is measured.

Real defects the acceptance run caught, all fixed:

1. Two syncs in the same second silently overwrote each other (timestamp PK).
2. Ordinals rendered "2th" and "3th" in Desk-facing copy.
3. Diffing week 1 against a PRESEASON baseline reported all twelve teams as
   standings movers, because preseason order is an arbitrary tiebreak among
   0-0 teams. Standings items now require games on both sides of the
   comparison. Item count on the real acceptance scenario: 25 -> 14 -> 10.
4. The existing Story Board was being flattened to a constant magnitude, which
   sank every matchup below every change item. Matchup candidates now carry
   their real Competitive Importance and Story Value into the ranking.
5. `/commissioner/inbox` raised NameError on a missing local import; the route
   tests now render every inbox surface.

Not yet done inside Tier 1: streak and all-play divergence live in the
snapshot payload but are not yet their own change items (they reach the inbox
through the existing analytics candidates). See ROADMAP.md.

## The All-City Team sidebar feature (2026-08-31)

**Status: built, validated, previewed. Nothing published.** The 2026 edition
is drafted into disco week-01 and still carries the ROUGH DRAFT marker, so it
is double-blocked from publishing (unapproved plus blocked marker) until
Jonathan edits it. The module is opt-in and is NOT included on any issue in
the real DB; include it on the Desk when you want it.

- `leaguepage/all_city.py` validates an edition and renders the table, the
  rule footnote and the near-miss list. `editorial/features/all-city/` holds
  the editions plus a README with the rule and the rerun procedure.
- New issue module `all-city` ("The All-City Team"), kind `all-city`, in
  `OPT_IN_MODULES`. Prose lives at `sections/all-city.md` like any other
  section, so the Desk editor needed zero changes.
- Editions bind to one `(season, issue_key)`; a rerun is a new file. There is
  deliberately no "latest wins" fallback.
- 2026 lineup: Josh Allen, Bijan Robinson, Jonathan Taylor, Ja'Marr Chase,
  Justin Jefferson, Colston Loveland, Brandon Aubrey. Five of the seven
  replaced the candidates in the original brief on consensus rank. Ja'Marr
  Chase's left knee (hyperextended 2026-08-25) is the one thing to re-check
  before this publishes.
- The rule is municipal class, never population (docs/DECISIONS.md). It costs
  the roster the consensus RB1, TE1 and TE2, which is the joke.
- **Second variant, same machinery:** module `all-city-marquee` ("The
  All-Marquee Team"), editions in `editorial/features/all-city-marquee/`, is
  the same exercise with `rules.minimum_population: 100000`. Both are drafted
  into disco week-01 and both are unpublished. 2026 marquee lineup: Josh Allen,
  Omarion Hampton, Bucky Irving, Drake London, Parker Washington, Tyler Warren,
  Tyler Loop. Two printed rulings there: the Washington, D.C. exception (the
  sources genuinely conflict) and reading the allied-cities clause as "no
  QUALIFYING U.S. city", which is what puts Drake London on Greater London.
- The 100k floor is expensive and that is the point: it costs the consensus
  RB1, RB2, WR1, TE3 and K1, and leaves two qualifying kickers in the whole
  league, both named Tyler.
- `tests/test_all_city.py` (55 tests) guards both shipped datasets, the
  exact-match rule, roster completeness, the population floor, the column
  allowlist, and the public/private field split.

## Remote authoring — Phase 2: Supabase auth (2026-08-31)

**Status: sign-in works end to end against the real Supabase project.**
Migration 0001 is applied; `scripts/verify_supabase_schema.py` reports
16/16 tables present and locked against the anon key. A real OTP was
requested and delivered, and the identity record now exists (confirmed by a
`should_create_user=False` probe returning 200).

`.env` exists at the repo root, gitignored (`git check-ignore` verified),
holding `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
`LEAGUEPAGE_COMMISSIONER_EMAILS`, `LEAGUEPAGE_AUTH_MODE=off` and a
generated `LEAGUEPAGE_SECRET_KEY`. Auth mode stays `off` locally so the
localhost Desk keeps working without ceremony; the hosted deployment sets
`required`.

Trap that cost a debugging cycle and is now covered by a test: `auth.py`
originally read `os.environ` directly, and nothing called
`settings.load_env()`. A `.env`-configured allowlist was silently ignored,
so the Desk looked configured while sitting in open fallback mode with an
empty allowlist. **All auth config must go through `settings.get()`.**

Design decisions worth not re-litigating:
- **Email OTP, not magic link, for Supabase.** OTP needs no Redirect URL
  registered in the dashboard, so sign-in works locally today and the
  hosted URL can be added later without code changes. Magic links can be
  layered on afterwards.
- **The OTP exchange is server-side.** The browser never receives any
  Supabase key, not even the browser-safe publishable one. Tested.
- **Two independent gates.** Supabase answers "who is this"; the
  `LEAGUEPAGE_COMMISSIONER_EMAILS` allowlist answers "may they use this".
  A valid Supabase user who is not listed is refused, and the address
  authorized is the one Supabase returned, never the posted form field.
- **One auth system.** Supabase plugs into the Phase 1 central route guard;
  there is no second session mechanism.
- **No email in URLs.** A signed short-lived HttpOnly cookie carries the
  pending sign-in, so addresses stay out of history and access logs and the
  allowed/rejected responses are byte-identical.

Schema: `migrations/0001_commissioner_state.sql`, applied. RLS is enabled
*and forced* on every table with a policy that consults `app_commissioners`
(not "any authenticated user"); `anon` is granted nothing on any table or
sequence.

**The one bootstrap step that cannot be automated:** `app_commissioners` is
empty, and because RLS is forced on that table and its own policy requires
membership, nobody can insert the first row. The anon key is granted
nothing, so the application cannot do it either — deliberately, since an
app that could write its own allowlist would not have an allowlist. Run
`.venv\Scripts\python.exe scripts\make_commissioner_seed.py`. With
`DATABASE_URL` set in `.env` it applies and verifies the seed directly;
without it, it writes the idempotent `insert` to
`backups/seed_commissioner.sql` (gitignored) and the clipboard when
available, for the Supabase SQL Editor. Needed once, and again whenever a
Commissioner is added.

Verified 2026-08-31 that this is a hard boundary, not an inconvenience:
`select` on `app_commissioners` with the publishable key returns **401**, so
the seed genuinely cannot be automated with the credentials on this machine.

Remaining before remote authoring is live:
1. Seed `app_commissioners` (above). Until then, Postgres-backed reads and
   writes return nothing even for a correctly signed-in Commissioner, which
   blocks every step below that needs to be *proven* rather than written.
2. Prose repository cutover (sections table replaces `editorial/**/*.md`) —
   the last structural blocker; do a fresh export first.
3. Durable jobs cutover (`jobs` table replaces `_JOB`/`_JOBS` globals —
   `publish_jobs.py:41` and `sync_jobs.py:30`).
4. The hosted Desk also needs the Sleeper cache (12,225 players, rosters,
   matchups) in Postgres, plus resolution of **47** filesystem write sites
   (recounted 2026-08-31) across `desk.py`, `desk_editor.py`, `dossier.py`,
   `issue_builder.py`, `mailer.py`, `matchup_packet.py`, `packet.py`,
   `publish.py`, `publish_jobs.py`, `review_packet.py`, `site_build.py`,
   `storage.py`, which a read-only serverless runtime will reject.
5. Private Vercel project + env vars; add its URL to Supabase Auth only if
   magic links are added later (OTP does not need it).

## Remote authoring — Phase 1 foundation (2026-08-31)

**Status: remote access is NOT live yet.** Local Desk is unchanged and
remains the only working authoring path. What landed is the security and
recoverability foundation plus the architecture decision (docs/DECISIONS.md).

Recon numbers that drive everything: authoritative Commissioner state is
**247 rows + 14 meta + 38 prose files (57 KB)** = 1.9% of the DB; the
other 12,749 rows are rebuildable Sleeper/archive cache. Heaviest read
0.05s. **Prose is filesystem-authoritative** and **job state is process
memory** — those two are the only structural blockers to cloud hosting.

Landed and proven:
- `scripts/export_commissioner_state.py` / `import_commissioner_state.py`
  — full backup of authoritative state; round-trip verified byte-identical
  by checksum into a scratch restore. `backups/` is gitignored (it holds
  prose and manager context). **Run the export before any migration.**
- `leaguepage/auth.py` — magic-link sign-in, server-side allowlist, single-
  use login tokens, signed expiring session cookies, CSRF, rate limiting,
  no open redirect, kind-separated tokens (a login token cannot act as a
  session), allowlist re-checked on every request so removing an address
  kills live sessions.
- Central `RequireCommissioner` middleware in `desk.py`: every route is
  private by default; the ONLY public paths are `/health`, `/login`,
  `/auth/request`, `/auth/callback`, `/static/sortable.js`.
  `tests/test_auth.py` enumerates the live app and proves 30+ routes
  reject anonymous callers — a new route cannot be born public.
- `leaguepage/mailer.py` — one seam, `log` backend locally (writes
  `logs/login-links.log`) and `resend` backend for hosting.
- Env gates: `LEAGUEPAGE_AUTH_MODE` (off|required),
  `LEAGUEPAGE_COMMISSIONER_EMAILS`, `LEAGUEPAGE_SECRET_KEY`,
  `LEAGUEPAGE_MAIL_PROVIDER`, `LEAGUEPAGE_MAIL_FROM`, `RESEND_API_KEY`.
  Default `off` keeps the localhost Desk working exactly as before.

Remaining, in order (each is contained; see DECISIONS.md for rationale):
1. **Prose repository** — put `editorial/**/*.md` behind a repository so a
   Postgres backend is a drop-in. Content model is only 4 path shapes:
   `lowdown/lowdown.md`, `sections/<module>.md`,
   `matchups/<slug>/draft.md`, `proposals/<section>.md`.
2. **Durable jobs table** — replace the daemon-thread `_JOB/_JOBS` globals
   in `sync_jobs.py` / `publish_jobs.py` with DB rows the browser polls.
3. **Email proposals** — signed opaque section tokens, inbound webhook with
   signature verification + sender allowlist + replay protection, proposals
   stored separately and never auto-applied to Commissioner Content.
4. **Deploy** — second private Vercel project, Supabase Postgres, env vars.
5. Remote publishing stays optional and last; local Publish & Deploy works.

**Blocked on Jonathan (Claude cannot create accounts):** a Supabase project
(Postgres + connection string) and a Resend account + inbound domain. Until
one of those exists, no remote path can be finished.

## Tuesday prep routine (cloud, 2026-08-30)

- Routine **"League-Page Tuesday prep"**, id `trig_01BfY734Z6uagVJQbXkSJL2J`,
  cron `0 16 * * 2` (12:00 America/New_York while EDT; **after DST ends
  2026-11-01 it fires at 11:00 local** — update to `0 17 * * 2` then).
  Manage at https://claude.ai/code/routines (deletion is only possible
  there; this session's tooling cannot delete routines).
- **It runs in Anthropic's cloud, not on this machine.** It therefore
  cannot sync the local DB, touch the Desk, or refresh the local editorial
  workspace. Attaching the private repo was REFUSED by the platform
  ("You don't have access to a repository this routine uses"), so the
  routine has no repo and works purely from the public Sleeper API plus
  web news — no private material leaves the machine, by construction.
- What it delivers each Tuesday: the Wednesday-evening deadline reminder,
  a post-mortem of the completed week, previews of the upcoming week,
  a per-team news sweep with sources, and factual award candidates. It is
  explicitly barred from writing prose in Jonathan's voice, publishing,
  and printing handles/real names.
- The local half of the workflow is unchanged and is what the reminder
  points at: launcher → SYNC SLEEPER → EDIT WEEK N. The Desk's ghost
  briefs remain authoritative; the cloud pack is extra ammunition.
- **Delivery: a mobile push.** The first test run sent one unprompted, so
  `allowed_tools` is evidently not a hard block; the prompt now requires
  exactly one push (the reminder plus a one-line summary) as its last
  step, and a hard rule forbids every other outbound channel: no email,
  no calendar writes, no Drive, no messaging anyone else, even though
  Gmail/Calendar/Drive connectors are attached by account default.
- Verified test run 2026-08-30 (`cse_017eMhgWf2XTJ2k3qCo9dswQ`): success
  in 240s / 36 turns. It correctly refused to invent a post-mortem or
  awards because the season opens 2026-09-09, produced both leagues'
  Week 1 pairings using team names with "Roster N" fallbacks, swept ~25
  players for dated, sourced injury news, and pushed the reminder.

## Author-feature rule + dark Surfeit theme (2026-08-30)

- **The author does not headline his own newsletter.** `League` gains
  `author_roster_id` (1 in both leagues — a publication fact; Sleeper's
  `is_owner` also flags co-commissioners, so it cannot be derived).
  `matchup_interest.author_matchup_stakes` allows FEATURE only with real
  playoff consequences: 4+ weeks played, within 3 weeks of
  `playoff_week_start`, and a team on the cutline (seed spots±1) or both
  holding berths. Otherwise `feature_blocked` is set and
  `recommend_prominence` hands FEATURE to the next matchup; his stays
  MAJOR. Matchup Lab shows the reason; commissioner override still wins.
  Playoff shape is read from league settings, never hardcoded.
- **Surfeit is now dark.** Palette taken from the HAF A5 Skunk Works
  Futures coin/badge Jonathan supplied: night navy `#071a2f`, coin gold
  `#facd00`, sky `#58b6f0`, steel `#1d3d63`, silver-blue `#9fb6cc`.
  Contrast measured on production-equivalent build: body 8.4:1, gold
  headings 11.5:1, links 14.7:1, REACH 8.7:1, STEAL 10.2:1. Masthead now
  uses the transparent roundel; `_theme.html` (legacy renderer) kept in
  step. Disco unchanged and still distinct (slate + amber vs navy + gold).

## Browser-only weekly workflow (2026-08-30, final tranche of the day)

- **SYNC SLEEPER button** on Desk home: `leaguepage/sync_jobs.py`, same
  async-job pattern as publish (POST returns instantly, polling UI,
  duplicate clicks join). One job = both leagues' Sleeper sync + snapshots
  + transaction contexts + automatic `refresh_issue_research` for every
  existing current-week workspace — the Build step is folded in, so
  briefs are fresh right after sync. Runs in-process (no subprocess, no
  shell). Home shows Synced timestamp, per-league summary (week, teams,
  new transactions, renames), Show Sync Details, and failure keeps the
  successful league. Terminal sync is fallback only (README updated).
- **Prose protection**: the refresh path is the Build button's own logic —
  commissioner_notes.md never overwritten, content files only created
  when absent; guarded by test_refresh_preserves_commissioner_prose.
- **display_name everywhere humans read**: matchup analysis teams now
  carry `display_name` (override > Sleeper name > "Roster N"); Matchup
  Lab titles, matchup detail headers, story-candidate headlines, and
  angle premises use it. Slugs (`roster-N-vs-...`) remain the stable
  internal identity for paths/URLs by design and never churn on renames.
- 206 tests. Measured workflow: launcher → Sync (1 click, ~3s) →
  Edit Week N (1 click) → click a textarea (1 click) → typing.

## Calibration tranche (2026-08-30, after the feature tranche below)

- K/DST verdict: overall FantasyPros ECR ranks special teams below the
  draftable range (best K #202, best DST #186 on the 1QB board), so their
  huge "reaches" are reference artifacts. Headline Biggest Reaches/Steals
  are skill-position only; K/DST get a Special Teams Outliers box with
  within-position context and a disclosure. Per-pick rows unchanged.
  Disco's superflex board (no K/DST, QB #1 overall) confirmed correct.
- Draft reference snapshots are hash-pinned in tests/test_calibration.py:
  re-importing rankings mid-season FAILS tests by design. A new season's
  board belongs under a NEW source key.
- Transaction engine: surplus = usable depth (startable-quality players
  beyond lineup demand); questionable needs a materially bad drop side +
  one more wrong signal; medium confidence says "One plausible rationale";
  low collapses to "Rationale unclear"; move lines are type-aware
  ("Claimed X for N FAAB"); dropped players now get reference-derived
  values (a real bug: they read 0, disabling drop-side checks); curated
  Force Flow is ordered by editorial priority (trade > FAAB > weakness
  fix > shift > churn).
- Sortable tables remember per-page sort in sessionStorage (Back keeps a
  comparison). Ghost lowdown briefs flag repetition when the boldest
  pick/best value already appears in 2+ published issues.
- Browser-pane note: the screenshot compositor reliably fails on scrolled
  positions of these pages; the workaround is a tall emulated viewport
  (resize_window height ~2400) and screenshots from scroll position 0.

## Draft value + transaction rationale + sortable tables (2026-08-30)

- **Draft value**: `leaguepage/draft_value.py` classifies every pick against
  the stored FantasyPros ECR snapshot (delta = pick_no − ref; negative =
  early). One full league round (total_teams, read dynamically: 12 Disco /
  10 Surfeit) = REACH/STEAL; ±2 picks = on board; between = EARLY/VALUE
  (shown as signed numbers, not labels). Visual intensity =
  |delta|/league_size capped at 3 rounds (`--dvi` CSS var + color-mix);
  words always carry the meaning. Surfaces: Draft page (Market Deviations
  top-5s, per-team tables, full board), Team page Draft Recap (biggest
  reach/steal + per-pick), ghost briefs (draft-capsules get reach/steal
  counts + consensus-following/defying tags). Methodology note on both
  pages says market value, not a draft grade.
- **Transaction rationale**: `leaguepage/transaction_analysis.py` infers
  plausible roster logic (weakness / depth / injury-drop / surplus /
  rebalance / streaming), labels no-story moves "Rationale unclear", and
  "questionable" only on ≥2 wrong-direction signals with no positive
  rationale. Wording is always "Likely/Possible rationale" — never manager
  intent; confidence is internal-only. Before/after positional ranks are
  persisted at sync (`txn_ctx:{league}:{txn_id}` meta,
  `record_transaction_contexts` called from scripts/sync.py; render omits
  the delta when no trustworthy context exists). Post-move outcome (starts/
  points since) is separate and never rewrites the original reading.
  Surfaces: Force Flow "Reading the Moves" + full log, Team page Key
  Moves, matchup + forceflow ghost briefs, story candidates
  (weekly_signals). FAAB now reads settings.waiver_bid (the old
  waiver_budget-only sum missed all waiver claims).
- **Sortable tables**: `static/sortable.js` (vanilla, progressive; served
  to the Desk at /static/sortable.js) — opt in with `<table data-sortable>`,
  th `data-sort-type`/`data-sort-dir`/`data-nosort`, td `data-sort-value`.
  Click cycle asc→desc→canonical; missing values sink; stable ties;
  keyboard + aria-sort. Sortable: teams matrix, standings (+under-hood,
  playoff via defaults), draft tables, team Draft Recap, Force Flow log,
  Desk team-names panel. Deliberately fixed: issue prose, curated
  editorial sections, the Desk power-ranking form (authorial order).
- 188 tests passing. Real-data checks: Jahdae Walker (Disco) = REACH ·
  244 picks early; Jordan James (Surfeit) = REACH · 131 early; Surfeit
  QB sort puts Los Bandidos (#1) first.

## GitHub + Vercel Git integration (2026-08-30)

- Source is hosted at **https://github.com/Biomusician/league-page-v2**
  (PRIVATE). Legacy public fork `Biomusician/league-page` (2022 Svelte
  project) is unrelated; never touch it. Local remote `origin` points at
  league-page-v2.
- Vercel project `league-page` is connected to that repo, **production
  branch `site`**. `site` carries only the audited `dist/` output;
  `scripts/push_site_branch.py` builds, audits, commits, pushes it, and a
  push auto-deploys production (proven: dpl_AzUYd7Hybjg71o4uuWV3cRSdPxYv).
  `vercel.json` on both branches disables `main` deployments so the source
  tree can never be built or served by Vercel.
- Vercel cannot rebuild the site (the build reads the private local DB);
  the audited artifact IS the deployment unit. Do not "fix" this by
  committing private inputs.
- The Desk's Publish & Deploy still uses the direct CLI path (Option A);
  the Git path is a proven alternative, not yet wired into the Desk.
- **`main` is pushed and tracks origin.** Before the push (Jonathan's
  call, 2026-08-30) the local history was purged with git-filter-repo:
  `commissioner_notes.md` and `PREP.md` stripped from all commits and two
  handle strings replaced in old blobs; commissioner notes are now
  local-only files like managers.json. Full pre-rewrite history lives in
  `League-Page-pre-github-history-2026-08-30.bundle` (repo root,
  gitignored, never push it). `scripts/audit_repo_privacy.py --history`
  must be clean before any future main push; it audits source history
  only (`site` is audited at build time, and handles in `archive/` are
  verbatim-public).
- Vercel credentials: real login done by Jonathan 2026-08-30 (`npx.cmd
  vercel login`). Claude sessions see a phantom overlay copy of
  auth.json; only Jonathan's own terminal counts (see DEPLOY.md).

## THE SITE IS LIVE

**Production: https://league-page-ten-sandy.vercel.app**

- Vercel project `league-page`, account biomusician (scope "biomusician's
  projects"), CLI authenticated on this machine. Deploy unit is `dist/`
  only; redeploy commands are in docs/DEPLOY.md.
- Deployed build: 98 pages + 3 logo assets, privacy audit clean, all 1,319
  internal links verified, 118 offline tests passing.
- Verified in production: root selector, both league homes, drafts,
  standings, teams + team pages, Common Tactical Picture, Disco archive
  (all 55 historical issues), both 2026 Draft Issue permalinks, both logo
  assets, mobile at 375px. Private-path probes (/.claude/, /editorial/,
  /CLAUDE.md, /docs/, /data/, /published/ sources, .vercel metadata) all
  return 404.
- Model authorization: Fable is authorized for MVP and maintenance work
  unless a later task explicitly requests Opus (Jonathan, 2026-08-29).

## What is published (content state)

- Both leagues have a published 2026 **Draft Issue containing the launch
  Lowdown only** (Surfeit: "Every Draft Is a List of Assumptions"; Disco:
  "Vol 7.I: Establishing the Picture" with Oregon Trail + BYEpocalypse
  callbacks). Snapshots frozen under published/, committed.
- The Surfeit hardware/capsules sections remain ROUGH drafts on disk,
  unpublished. Disco capsules not written. See POST_MVP.md.
- **8 rosters use neutral "Roster N" commissioner overrides** (surfeit
  2/4/5/6/10, disco 6/10/11) because their managers set no Sleeper team
  name. Set real names on the Desk team-names panel, then rebuild+deploy.
- Logos (from Jonathan): static/disco-logo-banner.jpg (dark masthead +
  root card), static/disco-logo-light.png (unused, kept for light
  contexts), static/surfeit-badge.png (Skunk Works roundel, transparent
  background for the dark theme; masthead + root card),
  static/surfeit-logo.jpg (superseded white-background version, kept).
  build_site copies static/ -> dist/assets/.

## Private/public boundary (non-negotiable)

Only audited `dist/` output is ever deployed. Never deploy or expose the
authoring repo, data/ (SQLite), editorial/, .claude/, published/ sources,
templates/, leaguepage/, scripts/, tests, the Desk, or the private history
bundle (League-Page-PRIVATE-history-backup-2026-08-29.bundle — never push
or reimport). The source repo has no remote and stays private; pushing it
anywhere still requires explicit approval. The build audits its own output
and fails on private material; `test_all_internal_links_resolve` guards
link integrity.

## The authoring experience (rebuilt this tranche)

- **Launch**: double-click `Launch Commissioner Desk.cmd` (repo root) or
  the "League Commissioner Desk" desktop shortcut. It health-checks
  (`/health`), handles port conflicts (already-running Desk -> just opens
  the browser; foreign process on 8026 -> nearby free port, clearly
  stated), logs to `logs/desk-startup.log`, and opens
  http://localhost:8026/commissioner when actually ready. Closing the
  terminal window stops the Desk (no zombie ports). The original defect:
  a stale process on 8026 made `scripts/desk.py` print the URL after a
  buried bind error and exit 0.
- **Issue Editor** (`/commissioner/{league}/{season}/issue/{key}/edit`,
  reached via EDIT ISSUE buttons on the Desk home): whole issue on one
  screen. Blockers panel with jump links / READY TO PUBLISH; per-section
  cards (capsules split per team on `###` headings) with debounced
  autosave + Save All, base-hash conflict detection (two tabs cannot
  silently clobber each other), per-section approve gated on blocked
  markers, Preview section, full private Preview (banner-marked),
  History (last 50 revisions per section in SQLite, Restore), Request
  rewrite -> `REVISION_REQUESTS.md` for Claude Code, side-by-side
  proposal review (`proposals/<section>.md`, Accept/Keep, never silent
  replacement), inline rankings table, team-name manager, Publish… with
  Publish Locally / Publish & Deploy (build + audit + Vercel + URL
  verification; stops at the first failed step).
- Commissioner edits mark a section `commissioner-edited`; authoring
  rebuilds only ever write briefs/AUTHORING files, never prose files.
- **Ghost-brief authoring model (Jonathan, 2026-08-30, canonical)**: empty
  sections start as a private ghost writing brief (leaguepage/
  ghost_briefs.py — strongest facts, angles, 0-2 callbacks, structure;
  ~relevance-filtered, computed live from synced data at page load, richest
  for weekly matchups via compute_week). First keystroke replaces the
  ghost; emptying restores it; the brief stays available under Writing
  brief / Show evidence. Ghost text is never content: it cannot save,
  snapshot, publish, or count as written (tested). Claude prose is
  OPT-IN via Request Claude draft / Request rewrite -> proposals reviewed
  side by side. Provenance migration 2026-08-30: unaccepted Claude-ROUGH
  files (surfeit draft custom/hardware/draft-capsules, surfeit week-01
  TEST matchup drafts) moved to proposals/ (matchup files as
  proposals/matchup--<slug>.md), snapshots kept in revision history;
  commissioner-authored and published prose untouched.

## Weekly issue cycle (the whole thing)

1. Double-click `Launch Commissioner Desk.cmd`.
2. Desk: issue workspace Build (packets + briefs), decisions, angles.
3. Claude Code: work the issue's AUTHORING_INDEX.md / matchup packets with
   the my-writing-style skill; later, "work all pending rewrite requests".
4. EDIT ISSUE: edit inline, approve sections, clear blockers.
5. Publish… -> Publish & Deploy (or Publish Locally + the manual CLI in
   docs/DEPLOY.md). Commit the new published/ snapshot.
6. Stale data? `.venv\Scripts\python.exe scripts\sync.py` first.

## Data state

- Sync current as of 2026-08-29: NFL preseason, fantasy week 1. Disco
  228/228 picks, Surfeit 150/150; Week 1 pairings exist for both.
- Reference ranks: FantasyPros ECR snapshots in refdata/adp/ (half-PPR for
  Surfeit, superflex for Disco). 1 unmatched Disco player (Will Howard),
  delta honestly omitted.
- Confirmed coalition mappings (Jonathan, 2026-08-29): FRA/UK = surfeit
  roster 8, JPN/SWE = surfeit roster 7. "EMCO" alias remains UNVERIFIED.
- matchup_interest fix this tranche: top-table/basement components require
  played games (preseason standings order is arbitrary).

## Compaction harness (settled — do not redesign)

- SessionStart hook, matcher "compact", re-injects .claude/COMPACT.md
  after every compaction. Session-scoped copy lives in Fantasy Bot
  .claude/settings.local.json (absolute path); the committed
  .claude/settings.json here carries the portable $CLAUDE_PROJECT_DIR
  form for sessions started in this repo. PostCompact stdout is NOT
  injected as context on Claude Code 2.1.247; do not "fix" this back.
- autoCompactWindow: 800000 in ~/.claude/settings.json (Fable 5 native 1M
  window; ~80%). Interactive equivalent: /autocompact 800k.

## Publish pipeline (job model, 2026-08-30)

Publishing from the editor is asynchronous: POST publish-start returns in
under a second, a daemon thread runs snapshot -> build/audit -> (deploy ->
verify), and the publish page polls publish-status, showing each stage
live. One job per issue at a time (duplicate clicks join the running job);
every child process runs stdin-closed with explicit timeouts and
tree-kill (`npx --yes vercel@latest ... --yes`); a failed or timed-out
stage stops the pipeline, is shown with Show Publish Details (log tail),
and never reports success. Logs: logs/publish-{league}-{issue}.log
(gitignored, credential-redacting). The deploy outcome lives separately
from the snapshot lifecycle in meta deploy_state:{league}:{season}:{issue}
("published" still means only "snapshot frozen locally"). Root cause of
the old hang: one synchronous POST held the browser through the whole
pipeline (30-90s, indefinitely if npx ever prompted on stdin) while the
publish actually succeeded, inviting double deploys.

## Comments (giscus boundary, not yet activated)

Published native issue pages can carry a giscus (GitHub Discussions)
comments section: config in leaguepage/config.py COMMENTS (all values
public client-side config; empty repo = disabled, the current state).
Per-issue thread identity is data-term "league:season:issue" (stable
across domain moves); imported historical archive pages never get
comments; per-issue opt-out via disabled_issues. Graceful failure: the
issue renders fully if giscus is down. Moderation happens in GitHub
Discussions on the comments repo. ACTIVATION (Jonathan, one-time): create
a PUBLIC comments-only repo (never the private source), enable
Discussions, install the giscus app on it, copy the four values from
giscus.app into COMMENTS, rebuild + deploy. Note: commenters need GitHub
accounts.

## Team analytics layer (2026-08-30)

leaguepage/team_analytics.py: deterministic positional strength (per-league
lineup demand read from the Sleeper payload; greedy optimal-lineup fill,
starters 0.7 / depth 0.3, fragility + surplus; preseason consensus-rank
values transitioning to a labeled in-season blend at 3+ played weeks),
recent form (last-3), meaningful streaks, transparent Monte Carlo playoff
outlook (observed scoring ONLY - no consensus ranks; too_early < 3 weeks,
bands < 8, percentages after; remaining schedule beyond sync simulated as
random pairings, disclosed), weekly analytics snapshots persisted by
scripts/sync.py (meta analytics_snapshot:{league}:{season}:{week}; week 0 =
preseason) so deltas are historical fact, and key-move detection. Public
surfaces: Teams comparison matrix, per-team Positional Strength +
Trend/Outlook/Key Moves, Standings movers/hot/trouble/Playoff Picture, all
with concise methodology notes. Editorial integration: matchup ghost briefs
carry ROSTER CONTRAST + recent shifts, the lowdown brief carries league
shift lines, and weekly_story_candidates gains analytics candidates
(playoff swings, movers, streaks, record/all-play divergence). Nav order:
Home, CTP, Peer and Near-Peer, Force Flow, Draft, Black Box, Standings,
Teams, Archive.

## Team identity (2026-08-30)

resolve_public_names precedence: commissioner override > Sleeper team name >
neutral Roster N; a neutral "Roster N" override yields automatically when a
manager sets a Sleeper name (that is how Los Bandidos surfaced); login
handles never become public names. The editor Team names panel shows the
Sleeper name, PRIVATE manager context (owners, co-managed flag, round-1
slot, top players), rename detection, and per-row "use Sleeper name"
(deletes the override; deliberately no bulk button so real overrides cannot
be mass-destroyed).

## Voice (authoritative)

.claude/skills/my-writing-style/SKILL.md — supplied by Jonathan, installed
verbatim, never regenerate. Weekly/draft authoring workflows are explicit
drafting requests (drafting override). style_check.py is warnings-only;
the skill-level sweep is authoritative.

## Top post-MVP tasks

See POST_MVP.md. Short version: real names for the 8 neutral rosters,
finish the Draft Issues, preseason Peer and Near-Peer, preseason Takes,
custom domain + one-command deploy.
