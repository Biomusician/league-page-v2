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

## 2026-08-31 — Hosted persistence talks to Supabase over PostgREST with the Commissioner's own token

The hosted Desk needs Postgres, and there were three ways to reach it: a
service-role key, a direct Postgres connection with `DATABASE_URL`, or
PostgREST carrying the signed-in Commissioner's JWT. Chose the JWT.

Why: it is the only option where the deployment holds no secret capable of
reading Commissioner data on its own. A service-role key bypasses RLS
entirely, so a single leaked environment variable would expose everything and
the forced-RLS work in migration 0001 would be decoration. A direct Postgres
connection has the same problem plus serverless connection-pool pain. With the
user's token, the policy in the database is the enforcement point, the same
allowlist decides access in the app and in Postgres, and revoking a
Commissioner in one place revokes them everywhere.

Consequence to plan around: a background job has no user token. Anything that
must run without a signed-in person (scheduled sync, queued publish) needs its
own answer, and the honest options are a narrowly-scoped server credential
used only by that path, or requiring the job to be started by a signed-in
session that supplies the token. Not resolved here; do not reach for the
service-role key by reflex when it comes up.

`DATABASE_URL` remains SECRET-class and local-only. It is used by migration
and seed tooling run from this machine and is deliberately NOT a runtime
dependency, so it never needs to exist in any hosting environment.

## 2026-08-31 — The allowlist bootstrap is a human step on purpose

`app_commissioners` cannot be seeded by the application, because RLS is forced
on that table and its only policy requires membership. This is not an
oversight to engineer around: an application able to write its own allowlist
does not have an allowlist. The first row is inserted with database owner
rights, either from the Supabase SQL editor or by `DATABASE_URL` tooling.
`scripts/make_commissioner_seed.py` generates it from `.env` so the address is
never committed and never retyped.

## 2026-08-31 — Sidebar features are data editions plus commissioner prose

The All-City Team is the first recurring sidebar feature, and it set the shape
for the ones after it. A feature edition is one git-tracked JSON file under
`editorial/features/<feature>/<edition>.json`, bound explicitly to a single
`(season, issue_key)` and optionally to a league list. `leaguepage/all_city.py`
validates it and renders the structured half (the roster table, the rule
footnote, the near-miss list); the surrounding copy is an ordinary
commissioner-owned `sections/<feature>.md`, so the Desk editor, the revision
history, the approval gate and the ROUGH DRAFT block all work with no new
plumbing. The module registers in `MODULE_DEFS` like any other and sits in
`OPT_IN_MODULES`, so an issue only carries it when the commissioner asks.

Why editions bind to one issue instead of "latest wins": rerunning the lineup
in week 8 must not retroactively change what week 1 published. A new run is a
new file with a new `issue_key`, the old file stays where it is, and the frozen
snapshot in `published/` was already immune anyway. Belt and braces, cheaply.

Why the data carries the rule and the code enforces it: `validate_edition()`
re-derives the exact-match test from the player's own name rather than trusting
the JSON, checks the roster against the declared format (no duplicate slots, no
missing K, no third running back), and refuses any qualification tier that
disagrees with the recorded census population. A malformed edition reports
`needs_review` on the Desk and renders nothing.

Why the public/private split is a field allowlist rather than a denylist:
`PUBLIC_ENTRY_FIELDS` is the only thing the renderer reads, so a private field
added to an edition later cannot leak by being forgotten about. Evidence IDs,
source URLs and research notes stay in the local file for pre-publication
review and never reach `dist/`.

## 2026-08-31 — The All-City rule is municipal class, not population

Every population floor we tried was arbitrary, and each one either threw out
Aubrey, Texas (5,006) or let in Gibbs, Missouri (village, 70). The rule that
survived red-teaming is the legal one: the place must be an incorporated
municipality its own state classifies as a **city**. Towns, villages, boroughs
and unincorporated communities do not qualify at any size.

The consequences are the feature. Chase, Kansas is a city of 396, so the
consensus WR1 starts. Bowers, Delaware is a town of 278, so TE1 sits. McBride,
Michigan is a village of 189, so TE2 sits. Gibbs, Missouri is a village of 70,
so the consensus number one overall player in fantasy football sits. A rule
that costs you the best player in the pool is a rule that is actually doing
something.

Population still appears, purely as the tier label (100,000+ Marquee City,
5,000–99,999 City, under 5,000 Technical Qualifier), and the validator enforces
that the tier matches the recorded number so nobody can promote a small town by
feel. A named Washington, D.C. exception was considered and dropped as
unnecessary: Washington, Pennsylvania is an incorporated city of 13,176, so the
name clears the default rule on its own.

## 2026-08-31 — Rules variants are sibling features, not a flag

The All-Marquee Team is the All-City Team with a 100,000 population floor. It
is implemented as a second module (`all-city-marquee`) of the same kind, whose
editions live in `editorial/features/all-city-marquee/`, rather than as a mode
switch on the first one. The feature key IS the module key, so the whole cost
of a variant is a directory and one line in `MODULE_DEFS`.

The alternative was one edition with a `variant` field and one module that
renders whichever the commissioner picked. Rejected because the two are
genuinely different articles that can run in the same issue, side by side, with
their own prose, their own approval state, and their own place in the running
order. A flag would have forced them to be alternatives to each other.

Two things generalized to make it work, and both are data-driven rather than
special cases: `rules.minimum_population` (validated, and it also forces every
starter to record a population), and `columns`, which picks the table's columns
from a known set. The marquee edition prints population where the parent prints
qualification tier, because in an edition where every row is a Marquee City the
tier column says the same thing seven times.

## 2026-08-31 — Two named rulings in the marquee edition, both printed

The 100,000 floor forced two calls that the parent edition never had to make,
and both are written into the data and rendered rather than buried.

**Washington, D.C. runs on a named exception.** The sources conflict on their
own terms: the Census Bureau's District of Columbia geographic guide records
that the District has one city, Washington, coextensive with it, and the
standard list of U.S. municipal corporations ranks Washington 22nd, while the
Bureau's population-estimates glossary treats the District as a county
equivalent and defines incorporated places without reference to it. Picking the
first reading is worth 93 places of receiver (Parker Washington at overall 65
instead of Denzel Boston at 158), which is exactly why it should not be silent.
It renders as a numbered footnote under the table so a reader can disagree with
it on the facts. The new `exception` field exists for this and nothing else.

**The allied-cities clause reads "no QUALIFYING U.S. city."** The premise
allowed U.S., French, UK and Swedish cities from the start, and in the parent
edition the clause never once decided anything, because London, Ohio (10,279)
covered Drake London on its own. Under the floor, London, Ohio fails and the
escalation hands over Greater London. Reading the clause as "no U.S. city of
any size carries the name" would have kept a WR8 out of the lineup on a
technicality about a village in Madison County, and would have left a rule in
the book that can never fire.

The floor is worth recording in one line: it costs the roster the consensus
RB1, RB2, WR1, TE3 and K1, and leaves exactly two qualifying kickers in the
league, both named Tyler.

## Publication quality gate: warnings are the override (2026-09-02)

A pre-publish checker that can be silently bypassed protects nothing, and one
that blocks on judgment calls gets turned off. So the severity model is two
levels with no third:

- **Blocker** — something no reader should ever see: an internal roster id or
  slug, a raw placeholder, markup that failed to render, a broken link, an
  included section with no copy, a private handle. Publication stops.
- **Warning** — a possible stale statistic, a probable typo, an inconsistent
  team name, a methodology tension. Publication proceeds. The Commissioner
  reading it and publishing anyway IS the override, so there is no override
  UI, no acknowledgement flag, and no way for a warning to become a blocker.

`privacy=True` findings are blockers with no path around them anywhere in the
codebase. The ignore store is consulted only for warnings, so dismissing a
finding can never unblock a publish even if the id is dismissed by hand.

**Voice is not a defect.** This is the constraint that decided every copy
detector. Fragments, slang, deliberate capitalization, invented compounds and
Air Force jargon are the product being sold; a gate with opinions about them
would be worse than no gate. Every copy check is therefore mechanical and has
one unambiguous correct form: a doubled period, a repeated word, a comma
where a full stop belongs, "way to many". Thirteen samples of real published
prose are pinned as must-not-flag tests, so a future detector that starts
homogenizing style fails the suite rather than the newsletter.

**Machine copy never imitates the Commissioner.** Scout View, the Model
Board, the front-page briefing and the team briefings all read like an
intelligent analyst's notes: no jokes, no analogies, no verdicts on people,
no predicted winners. This is not timidity — it is what keeps his voice worth
something. A site where everything sounds like him is a site where nothing
does.

## Corrections are additive; the original snapshot is never rewritten (2026-09-02)

Published issues are immutable, and fixing a typo must not cost that. A
correction is a NEW file (`draft.r2.json`) beside the original carrying
`revision`, `revises`, `revision_note`, `original_published_at` and
`revised_at`. The site renders the newest revision and prints "Updated <date>
· <note>"; the original stays on disk and in git as the record of what
actually went out that day.

Rejected alternatives: editing the snapshot in place (destroys provenance and
makes git history lie about what was published), and keeping a separate
errata page (nobody reads errata; the reader needs the corrected text where
the mistake was).

`scripts/apply_qa_fixes.py` is deliberately narrow — it applies only findings
carrying an exact `fix_from`/`fix_to` pair, and only the COPY category ever
carries one. What a team is called and what a number means are the
Commissioner's calls. A test asserts that no finding outside COPY can offer
an automatic fix, so widening the script's reach requires changing that test
on purpose.

## Analytical rank is not editorial importance (2026-09-02)

The positional profile ranks every room a league starts, K and DEF included,
and that is correct: it is a fact about the roster. But "K is your
second-best strength" is not a headline, and it was one — team pages led with
Ka'imi Fairbairn and Cam Little as Biggest Reach because overall ECR puts
every kicker below the draftable range, making an 80-pick "reach" a fact
about the reference board rather than a roster decision.

The split: analytical surfaces (tables, full boards, per-pick rows) rank
everything; editorial surfaces (headlines, briefings, front-page items, Scout
View, Model Board) lead with QB/RB/WR/TE. A special-teams room reaches a
headline only when it is league-best or league-worst, and never displaces a
skill-position line. `draft_value.SKILL_POSITIONS` is the single definition
both halves read.

## Repetition: facts may repeat, callbacks may not (2026-09-02)

"Team 1 leads 2–1" is the current state of a rivalry and belongs on the
matchup page every week it is true. A quote from a 2019 newsletter read two
weeks running stops being a callback and becomes furniture. So the
suppression is asymmetric: a recently-surfaced archive quote is dropped from
the candidate list outright, while computed facts are merely demoted.

Surfacings are recorded per WEEK, not per build (`history_shown:` and
`receipts_shown:` in meta), which is what makes rebuilding or redeploying the
same week idempotent — the alternative, a counter, would exhaust every
callback in a league by the third redeploy.


## Takes: he marks them, the engine only ever recommends (2026-09-03)

The receipts engine shipped in the previous tranche extracted claims from
published prose with a regex. Across two real issues it produced exactly one
live receipt, and the failure was not the regex — it was the premise. A
receipt is funny because somebody stuck their neck out on purpose, and a
machine cannot tell the difference between a sentence that was a bet and a
sentence that was just a sentence.

So a Take is created by a deliberate act, and two database columns keep the
authority where it belongs. `status` is the Commissioner's verdict.
`recommended_status` is what the engine computed. They are never merged: an
engine that leans one way while he holds the other is a visible disagreement
in the ledger rather than a silent overwrite, and that disagreement is
frequently the interesting part.

`public` defaults to 0 and only a deliberate act sets it. The consequence is
that with no approved takes the front page shows no receipt at all, and that
is the correct output rather than a gap to fill.

**Two gates before any evidence hook runs**, both answering TOO EARLY rather
than guessing: the take's own review horizon, and a per-topic sample floor.
A take that loses one week is not wrong, and a system that says so stops
being funny and starts being stupid. Playoff claims need six played weeks;
positional, draft and transaction claims need three; a matchup prediction
resolves on the actual result immediately, because that is what it was.

**Draft claims never re-classify REACH or STEAL.** That comparison is between
one selection and the reference board on the day, it is immutable market
analysis, and re-scoring it later would be rewriting history to win an
argument. What is testable is whether the player is still on the roster,
whether he starts, and what he scores.

The calibration decision reaches all the way into this feature. Special-teams
players are named in evidence but never carry a verdict — kickers start every
week, so "did he start" says nothing about a claim — and the retroactive
candidate scan refuses to offer a claim that is only about a kicker or
defense "premium", because that measures the reference board's shape rather
than a roster decision. Enforcing it at capture time means the error cannot
re-enter through the take ledger the way it entered the 2026 Surfeit
rankings.

## A paraphrase is never presented as a quotation (2026-09-03)

The Commissioner can edit the text when he tracks a take — trimming a
sentence to its claim is a reasonable thing to want. That makes the stored
quote no longer his published wording, and an archive that presents it inside
quotation marks anyway is putting words in his mouth in his own record.

`verbatim` is therefore decided by the system, not by a checkbox: the quote is
compared with the section source at creation. A paraphrase renders as "he
wrote this, in substance" with no quotation marks, and `pubqa.check_receipts`
makes presenting one as a quotation a publication blocker rather than a style
preference.
