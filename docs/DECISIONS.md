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


## The schedule was always there (2026-09-03)

The playoff model paired the league at random for every remaining week and
said so in a note under the table. The reason given in the code was
"schedule beyond sync unknown", and that had been true for exactly as long
as nobody checked: Sleeper serves the pairings for any week you ask for,
filled with zeros until they are played. One league's week 3 was sitting in
the database, fetched before week 1 kicked off, with complete `matchup_id`s
and no points.

So the constraint was in the sync loop, not in the data. `ingest` asked only
for weeks up to the current one.

This matters beyond the odds themselves. A model that does not know who is
playing whom cannot answer "how much does Sunday move my odds", which is the
question a manager actually has, and it cannot answer "who should I be
rooting for", which is the question that makes a league chat fun. Both fell
out of the same simulation run once the schedule was real.

The lesson worth keeping: a disclaimer that has been in the code for months
is not evidence that the limitation is real. It is evidence that somebody
wrote it down once.

## The engine recommends, the gate stops crying wolf (2026-09-03)

Five publication blockers had no override path and fired on sentences that
were fine: "a classic man-vs-machine week" read as a leaked internal slug,
a code span containing `**` read as bold that failed to render, "playoffs
are coming soon" read as a stub, "weeks XXX through XXI" read as a
placeholder in a newsletter that numbers its volumes in Roman numerals.

A gate that blocks a good sentence gets switched off, and then it protects
nothing. Every one of those was narrowed so the real defect is still caught:
a genuine matchup slug has a multi-segment side, a genuine placeholder sits
alone on a line, a heading indented four spaces still trips the
unrendered-heading check because that is exactly what it looks like.

The same pass added two misses it should always have caught: a lowercase
`roster 4` (the flagship identity blocker was case-sensitive) and a relative
image source, which points at a directory the published page is not served
from.

## An alias is only private until he publishes it himself (2026-09-03)

The build audit read Sleeper handles out of `managers.json` and nothing
else. Aliases are precisely where real first names and nicknames live, so
the strings most likely to identify somebody were the ones the audit could
not see.

Adding them flagged 103 violations on a clean build, because half those
aliases are not private at all: managers put their own nicknames in their
team names. A name he published himself is his to publish.

So the audit subtracts anything appearing inside a current public team name,
compared on a normalised form, because an alias is often the slugified team
name and that slug is in every URL on the site. Aliases are only scanned
when the caller can say what the public names are; without that list the
audit falls back to handles alone rather than failing a build on its own
team names.

## A republish is not a correction (2026-09-03)

`published/` exists so that what shipped that day is still on disk.
`publish_assembled_issue` overwrote the snapshot in place, with no revision
and no "Updated" line, and the ordinary way to reach that was not malice: a
deploy that fails after the snapshot stage gets retried, and the retry
re-entered the same function.

An identical re-entry is now a no-op, so retries still work. A changed one
is refused and pointed at `revise_issue`, which keeps the original and adds
a sibling with a note. The distinction is the whole promise of the
directory: an archive you can quietly rewrite is not an archive.

## Hindsight goes in its own column (2026-09-03)

Two ledgers landed this run, and both had the same temptation: the
transaction rationale recorded at the time, and the REACH/STEAL call made on
draft night. Both would look smarter re-scored with the benefit of results.

Neither is. The rationale is what the roster said when he made the move, and
rewriting it to match the outcome invents a reason he never had. REACH and
STEAL compare one selection against the reference board on the day, which is
immutable market analysis; a reach that worked out was still a reach.

So "how it aged" is a separate column that answers a different question in
the reader's own vocabulary: is the player still here, does he start, what
has he scored, did the room move. Two thirds of that is answerable before a
game is played, which is why the draft version ships in August rather than
waiting for week 3.

## A line break he typed is a line break he gets (2026-09-04)

Markdown folds a single newline into a space. That is right for prose
composed in a text file and wrong for prose composed in a box on a screen,
which is where this newspaper is written. Stanzas, one-line verdicts and
lists of names that are not bullet lists all arrived on the page as a wall.

So `nl2br` is on, in one renderer (`leaguepage/prose.py`) that the Desk
preview, the full-issue preview, publication QA and the site build all
call. A preview that disagrees with the page is worse than no preview.
The generated review packet is the deliberate exception: it is hard-wrapped
for reading in a diff, and honoring its breaks would only make it ragged.

The cost is paid once and it is why `scripts/reflow_prose.py` exists.
Prose already on disk had been hard-wrapped near 78 columns, and those
wraps would have rendered as ragged lines. The migration joins a break only
where the sentence runs straight through it, then infers the rest of a
paragraph from one certain wrap, because a paragraph is wrapped or it is
not. It leaves bullets, headings, tables, code, and any paragraph
containing a short line, and it refuses any file whose rendered page would
move. **Editorial prose is now stored one line per paragraph.** Anything
writing a section file should soft-wrap, not hard-wrap.

## The parent of a section is the section it publishes inside (2026-09-04)

Matchup previews were peers of Common Tactical Picture in the authoring UX
and children of it on the page. They are children. A preview has no
standing alone, it publishes inside CTP, and CTP is finished exactly when
its previews are.

The relationship therefore lives in `module_states`, not in a stylesheet:
`matchup_children()` returns them, readiness reads them, the approval gate
is that they are all approved, and the editor nests the cards inside the
parent's `<details>`. Two bugs fell out of stating it that way. Approving
CTP was impossible, because the gate read `sections/ctp.md` for emptiness —
a file the module has never had; the prose gate now applies only to modules
that own prose. And bulk approve had to run deepest-first, or a parent is
asked before the children its gate depends on.

## An empty section is his call, not the system's (2026-09-04)

Modules used to remove themselves: Intel Prep and Branches & Sequels both
excluded themselves before week 5, on the reasoning that playoff leverage
computed off four games is fake precision. The reasoning is right and the
silence was not, because he never learned the section had been considered.
The module stays in, prints the reason, and dropping it stays a click he
makes. Explicit decisions are preserved in both directions through Sync.

## Weekly Hardware closes the paper (2026-09-04)

The weekly issue runs **Lowdown → matchups → custom section(s) → the
standing sections → Weekly Hardware**, and Hardware last is an invariant
rather than a default. A saved `position` used to win the module sort
outright, so a row written at any time could put any section anywhere,
including in front of the closer. Position now orders custom sections
against each other, which is the one place the Commissioner sets it, and
`_order_rank` gives Hardware a rank nothing else can reach.

The earlier guidance to write the Lowdown last is superseded. The Lowdown
and the matchups are the two highest-priority weekly writing surfaces and
the editor is ordered accordingly.

Placement note worth revisiting: the canonical order puts *Custom* at
position 3, so custom sections sit immediately after the matchups and
before the standing sections. That is the literal reading of the
Commissioner's numbering. Moving them after the standing sections is a
one-line change to `WEEKLY_ORDER`.

## A custom section is a row, not a schema (2026-09-04)

`issue_modules` already had `custom_title` and `position`, so making
special sections repeatable needed no migration at all. The first keeps
the plain `custom` key the single section has always used -- which is why
the prose already on disk did not move -- and the ones after it are
`custom-2`, `custom-3`. `next_custom_key` counts from the keys that
exist rather than from how many there are, so deleting the middle of three
cannot hand the next section a key already taken.

Nothing creates them in advance. A week arrives with no custom section
until he makes one, which is what stops every issue carrying an empty one
to ignore. There is no delete: excluding keeps the prose and takes the
section out of the paper, and a button that can destroy writing is not
worth the two clicks it saves.

## Retired, not deleted (2026-09-04)

Force Flow, The All-City Team and The All-Marquee Team keep their registry
entries and their rendering code. An issue that already published one still
has to assemble and render, and Disco's Week 1 Force Flow was written
before it stopped being a weekly section. So a retired module is never
offered on a new issue, an issue that already carries one keeps it, and
that copy publishes while sitting off the checklist and outside the
approval count.

Force Flow left for a different reason from the other two. It is a standing
league page built from synced transaction data -- it always was, at
`/{league}/transactions/` -- so asking him to rewrite it weekly was asking
for work the data already does. All-City and All-Marquee folded into the
generic custom-section primitive, which does the same job without two
bespoke modules to maintain.

## Provenance is recorded, never detected (2026-09-04)

A reader is entitled to know when nothing on a page passed through a
person, and the honest way to answer is to record it when it happens. A
detector would eventually be wrong about the Commissioner's own prose, and
being wrong in that direction is worse than saying nothing.

So accepting generated text stores the generator, an input class, and a
hash of exactly what was accepted. The section is fully AI-generated for as
long as its current text still hashes to that value: one edited character
retires the claim, and nothing has to notice or clean up after it.
Provenance freezes into the published snapshot beside the text it
describes, because the claim is about a particular text rather than about
a section key.

Two constraints fall out of that. `method` is a key into a fixed table of
input-class descriptions rather than free text, so a path, a prompt or a
private brief cannot be stored in it even by accident. And a generator we
cannot identify is reported as unknown rather than guessed, because a wrong
badge is a false statement about who wrote something. Force Flow's reading
of the week is arithmetic rather than a language model, so it says
"generated automatically" instead of wearing a Claude badge it did not
earn.

## Force Flow flags what is unusual for this league (2026-09-04)

"A big FAAB bid" is not a fixed share of budget. It is a bid this league
would find large, so the spend flags read the distribution of the league's
own completed bids rather than a number somebody typed once. Every flag
carries the evidence that produced it, and a flag that is a reading rather
than an observation says so on its face: the block detector states outright
that it is not proof of a block.

Internally a move can be called questionable. In public the evidence goes
out and the Commissioner's own voice does any roasting. A note is optional
on every flagged move and a missing one is never a blocker.

## Identity is reconciled across stores, not matched by name (2026-09-04)

Four stores describe an owner and none is a foreign key to the others:
Sleeper's users and rosters, the confirmed public name in `team_names`, and
the callsign and roster binding in `editorial/managers.json`. They join on
the Sleeper user id, and nothing checked that the join held.
`leaguepage/identity_audit.py` reconciles them; every finding names two
stores and the stable id they disagree about, and none of it comes from
comparing names for similarity, because deciding that two records are the
same person is a factual claim only the Commissioner may make.

**The Surfeit's canonical callsign is Seebass.** The superseded spelling
lived in exactly one authoritative place, the confirmed public name for
Surfeit roster 7; the Sleeper handle and the manager key were already
correct. Renaming changes the public team slug, so a link already shared to
`/surfeit/team/wild-seekats-seabass-kats/` will 404.

## 2026-09-04 — Automation supplies the default; it never takes the pen away

Every section the newsletter publishes exists on a spectrum between "he typed
it" and "code assembled it", and the Desk had started treating the second end
as finished business. A card said `Rendered automatically; nothing to write`.
Common Tactical Picture read `6 / 6 approved` and looked shut. Peer and
Near-Peer read its optional blurb from `sections/power.md` at publish time and
offered no way to edit that file. Force Flow, retired as a weekly section but
still carried inside an already-written issue, published prose the Desk would
not open.

The rule now is one sentence: **public prose is his; computed results are
not.** Anything that reaches a reader as writing can be changed from the card
it appears on, without a hidden route, a terminal, or a text editor pointed at
the repository. Anything that reaches a reader as a number — a score, a
standing, a rank, an award result, a FAAB figure — is shown beside the writing
as evidence and is not editable there, because changing how a section reads is
a different act from changing what happened.

Weekly Hardware is where the two meet, so it gets both halves explicitly. The
decided awards and their computed basis are shown read-only. A deterministic
composition of exactly those results is available as the section's default, to
take as a starting point or to return to. His override replaces the prose and
never the evidence, and the previous text goes to History first, so "reset to
generated" cannot destroy writing.

Common Tactical Picture keeps no second copy of anything. Its optional opening
remarks are a normal section file that publishes above the previews; the
previews remain the section's substance, and a blurb with no approved previews
under it does not publish at all.

## 2026-09-04 — An approval is about a text, so an edit retires it

`editor_save` had a comment saying approval must be re-asserted and a `pass`
underneath it. Approval survived every edit, which meant a section could
publish text nobody had signed off while the Desk showed a green chip saying
otherwise. Editing now takes the approval back, marks the card as changed
since approval, and requires him to approve what is actually there.

A matchup preview takes Common Tactical Picture's sign-off with it. CTP has no
text of its own — it publishes the previews — so approving it was approving
exactly that writing.

The mark is a stored flag rather than a comparison, because by the time the
page renders, the text that was approved is gone and nothing on disk can still
answer the question. It clears when he makes an approval decision either way.

## 2026-09-04 — Composed copy is machine-written, and says so

Provenance already distinguished "a model wrote this" from "nobody edited it".
Weekly Hardware's generated copy needed a third answer: our own code composed
it from results, so badging it "by Claude Code" would name a writer that was
not involved. `provenance.DETERMINISTIC` is stored like any other generator and
renders through `describe_machine`, which claims only that a machine produced
it and from what.

Restoring a generated version re-validates its badge only through hash
equality, never through resemblance. That is why resetting the Lowdown to its
Claude rough draft creates no claim: nothing recorded that the draft was
Claude's, and inventing the fact from the workflow's usual shape is the
detector we deliberately do not have.

A Commissioner intro inside Common Tactical Picture removes CTP's badge
outright, even when every preview under it is untouched. The badge describes
the section a reader sees, and that section now contains his sentence. No badge
is never a false statement; that one would be.

## 2026-09-04 — The Draft page is a data page and takes a data measure

The site's 52rem reading measure is right for prose and was wrong for the
one page that is mostly tables and side-by-side lists: on a wide monitor the
Draft rendered as a 700px article with two-thirds of the screen empty, and
Biggest Reaches and Biggest Steals sat in cards so narrow that every entry
wrapped onto four lines.

The container width is a per-page choice, not a global one. `base.html`
exposes a `main_attrs` block; the Draft page asks for `class="wide"` and
gets 84rem (about 70% of a 1920px display; not full-bleed). Every other page
keeps 52rem, and prose inside the wide page — the recap callout, the method
note — is capped back to a 60rem line by `.measure`, because a data page is
not licence for 1300px sentences.

Inside it, the hierarchy is the one a reader of draft analysis wants: a
strip of labelled facts (status, picks, teams, rounds, format) instead of a
status sentence; the Commissioner's recap as a quiet labelled callout rather
than an underlined line floating between sections; Market Deviations as the
lead analytical feature with reaches and steals side by side from 1024px up,
each entry three scannable lines (player / team · pick · reference /
verdict); special-teams outliers spanning beneath, not squeezed in as a third
column — and sitting beside the one headline list when only one exists, so
no draft ever leaves an empty column; team tables two abreast; the full board
at full width. Grid minimums use `min(30rem, 100%)` so a 320px phone never
scrolls sideways, which measurement confirmed at 320/375/430/768/1024/1440/
1920.

Off-board picks no longer appear under Biggest Reaches. They carry no
magnitude by design, and listing one as a headline reach with the verdict
"outside the reference board's range" contradicted the page's own footnote.

## 2026-09-04 — Force Flow leads with who did it

A reader's first question about any move is which team made it, and the tab
answered it last, or not prominently. Every surface now leads with the team:
the Commissioner's selections, the machine reading, and the log, whose
columns run Week · Team · Move · Added · Dropped · FAAB — the week is what a
reader scans by, and inside a week the team is what they scan for. Sort
semantics are unchanged.

Team identity is structural. A move carries its roster ids, resolved to
canonical public names and linked through the page's own team map; a trade
names both sides with ↔. Nothing recognises a team from a sentence, which is
why "Moves That Mattered" changed its source: it used to render the raw prose
of an issue's Force Flow section, which is opaque. It now reads the story
decisions behind each published issue — the Commissioner's `include` rulings,
keyed by the move's own candidate id — and joins them to the synced log on
that exact id. The candidate id has one definition
(`transaction_analysis.story_candidate_id`) shared by the Desk that offers a
move and the site that reads the ruling back, so the join is an equality
test, never a resemblance. A selected move the log no longer contains is
omitted rather than reconstructed. An issue whose Force Flow prose has no
structured selection behind it shows that prose as published, labelled with
its issue, and that is the whole extent of the fallback.

## 2026-09-04 — Force Flow is a standing tab, not an archive section

Force Flow stopped being a weekly section but issues written before that
still carry one, and published snapshots are immutable. Reprinting a standing
feature inside every back issue is not how a newspaper works, and showing the
same prose once in the archive and again on the live tab is worse.

So this is a presentation rule, not a data change. `PERSISTENT_TAB_MODULES`
names the module; `_issue_ctx` skips it when rendering any published issue,
the home page's "In this issue" list follows, and the snapshot file is not
touched — the Disco Week 1 file's hash and mtime are identical before and
after a build, and it still contains the section. The tab may consume that
history as evidence; it is simply not itself archived as a weekly section.
The archive index never had a Force Flow entry, and now has a test saying so.
