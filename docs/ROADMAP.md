# Roadmap

Ranked product roadmap. **Future work only** — what exists today is in
`docs/HANDOFF.md`, and why it was built that way is in `docs/DECISIONS.md`.
Keep those three separate: HANDOFF gets stale if it doubles as a wish list.

Reviewed 2026-09-03.

## The product rule

Sleeper already gives the league rosters, standings, scores, transactions and
player lists. League Page earns its place by answering what Sleeper does not:
what changed, why it matters, what is unusual, what to watch, what history
says, and what the Commissioner should consider writing about. A feature that
mainly reproduces Sleeper gets rethought or dropped.

Private side, the equivalent test: does this remove research, navigation, or
repetitive judgment from the weekly process? If not, it is not Tier 1.

## The Commissioner Portal

Target architecture, transition order and the manual gates live in
`docs/COMMISSIONER_PORTAL_ARCHITECTURE.md`. Ranked tranches, scored
(Impact x Prerequisite value x Risk reduction) / Cost on 2026-09-05:

| # | Tranche | Score | Status |
| --- | --- | --- | --- |
| 1 | Canonical preview (one renderer) | 32 | `shipped` |
| 2 | Issue Room shell | 19 | `shipped` |
| 3 | CTP single approval | 16 | `shipped` |
| 4 | Durable jobs table | 13 | `planned` |
| 5 | AI WritingPacket + proposal queue UX | 10 | `partial` — the packet exists; the queue does not |
| 6 | Prose repository boundary | 10 | `planned` |
| 7 | Cloud persistence (Supabase Postgres) | 9 | `planned` |
| 8 | Hosted private beta | 5 | blocked on the manual gate |
| 9 | Cloud publication worker (GitHub Actions) | 2.4 | `deferred` until 6 and 7 |
| 10 | Portability / onboarding | 1 | seams only, no SaaS |

Durable jobs outranks the prose repository because it pays off locally on
its own: a publish survives a Desk restart today, not only after a cutover.

## Sequencing gate: remote authoring

Remote authenticated authoring is **not live**, and it is blocked on a step
only Jonathan can perform: `app_commissioners` must be seeded with database
owner rights (`scripts/make_commissioner_seed.py`). RLS is *forced* on that
table and its policy requires membership, so the application cannot seed its
own allowlist by design.

Everything else in the Phase 2 list is code, and none of it is blocking Tier 1
because Tier 1 adds **no filesystem state**: its durable state is rows in
`sync_snapshots` plus the existing `story_decisions` table. Both are in
`migrations/`, in the export/import bundle, and in the schema verifier, so the
cutover surface did not grow.

Structural blockers still open, in order:

1. **Seed `app_commissioners`** — Jonathan, once. Blocks proving anything below.
2. **Prose repository** — `editorial/**/*.md` behind a repository interface so
   Postgres is a drop-in. Four path shapes. The last structural blocker.
3. **Durable jobs table** — replaces the `_JOB`/`_JOBS` process globals in
   `publish_jobs.py` and `sync_jobs.py`, and the login rate-limit
   dictionaries in `auth.py`.
4. **Filesystem write sites** — measured 2026-09-05: 62 in `leaguepage/`, of
   which ~24 are persistent editorial state a read-only serverless runtime
   rejects. The rest are build artifacts, immutable publication records, or
   recomputable research, and do not all need a database.
5. **Private Vercel project + env vars.**

## Status key

`shipped` · `partial` (usable, not finished) · `planned` · `deferred`

---

## Tier 1 — the weekly triage loop

The target experience: **Sync → What changed? → Top stories ranked → Issue
research refreshed → Write.**

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 1 | **Commissioner Change Inbox** | `shipped` | sync snapshots | Replaces opening five pages and diffing by eye after every sync. |
| 2 | **Postgame issue auto-refresh** | `shipped` | matchup packets | One issue that evolves, rather than a preview workflow and a disconnected recap workflow. |
| 3 | **Story Significance engine** | `shipped` | 1, matchup interest | Separates an interesting fact from a newsletter story, and says why. |

Shipped in this tranche as one coherent workflow. See HANDOFF for what each
piece actually does and where.

**Known follow-ups, not blockers:**

- Streak and all-play divergence are captured in the snapshot payload but are
  not yet emitted as their own change items; they currently reach the inbox
  through the existing analytics candidates.
- The inbox merges change items with the weekly Story Board. The older
  `/stories` Story Board page is now redundant for weekly issues and should be
  folded into the inbox once the inbox has survived a few real weeks.
- Draft-receipt change items (a Reach suddenly performing) are Tier 4 #14.

**Morning Force Flow loop (explored 2026-09-05, `planned`):** both leagues
run FAAB with daily waivers on (the synced settings say `daily_waivers=1`,
Disco clearing at hour 5 and Surfeit at hour 2), so "after waivers run" is
every morning. What already exists: the public Force Flow tab renders every
completed move with a deterministic reading and the Commissioner's per-move
note, the Desk Force Flow page has a note box on every row, and the Command
Brief's MARKET section ranks the new moves. What is missing, in order:

1. An unattended morning sync (Windows Task Scheduler running
   `scripts/sync.py`), which is a machine-level change and his to approve.
2. "New since the last deploy" on the Desk Force Flow page: the moves that
   arrived after `last_public_change`, note boxes first, so the morning is
   three notes and a click rather than a scroll.
3. A tab-only deploy: build, deploy, verify, with no snapshot stage, so the
   standing tab can go out without an issue publish. Today the unchanged-text
   path of an issue publish does this by accident.
4. Draft notes, never auto-published: a Claude Code morning pass reads the
   brief's MARKET section and writes note proposals into the Desk for him to
   accept or discard. The deterministic reading publishes on its own; prose
   waits for him, per the standing rule.

**Deferred from the overnight editorial pass (2026-09-04):**

- Source disagreement: import a second reference (rest-of-season ECR or a
  projection set) so the Command Brief's SOURCE DISAGREEMENT section can
  measure a gap instead of saying it cannot. `planned`.
- Team-name history on sync: keep every Sleeper team name a roster has
  carried, so a former name in old copy (Disco Week 1 Hardware's "George &
  Friends") is matched structurally rather than by elimination. `planned`.
- Cross-league lane reuse: Tracks and Fades draw the same national-source
  paragraphs into both papers. The coherence check reports it; the section
  authoring workflow should prevent it with a per-league angle. `planned`.

---

## Tier 2 — the consumer half

Do not start until Tier 1 has been used in a real weekly cycle.

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 4 | **Personalized "Your Team"** | `shipped` | none | `team_briefing.py`: the briefing, editorial weighting, season-stage section order, storyline, quality-ranked mentions. |
| 5 | **Playoff scenario explorer** | `partial` | 4 | `team_analytics.playoff_outlook` already produces bands and percentages with an honest too-early stage. Missing: per-game leverage and rooting interest. |
| 6 | **Transaction decision ledger** | `partial` | none | `transaction_analysis` already snapshots rationale and tracks outcome and rank shift. The at-the-time reading is now public on Force Flow, team briefings and Scout View. Missing: the how-it-aged column, which needs played weeks. |
| 7 | **Matchup forecast + upset path** | `partial` | 5 | Structural edge, vulnerability, form and receipts all exist. Missing: the upset-path narrative. |

Recommended order within Tier 2: **6 → 4 → 7 → 5.** The ledger is closest to
done and produces newsletter material immediately; the scenario explorer is
the most work and the least useful before week 6.

---

## Tier 3 — depth

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 8 | **Receipts / takes tracker** | `shipped` | 1 | `takes.py`: Track This Take, seven-status lifecycle, six evidence hooks with horizon and sample-floor gating, retroactive candidate scan, Change Inbox integration, gated public receipts. The engine recommends; the Commissioner rules. |
| 9 | **Smart archive recall** | `shipped` | none | `history.py` adds prose-quality filtering, whole-sentence quotes from the issue body, and asymmetric repetition suppression on top of `story_memory`. Live on Disco matchup pages. |
| 10 | **Consumer email digest** | `planned` | remote auth, Resend | Separate data model from Commissioner mail. Never send whole issues; the site stays canonical. |
| 11 | **Trend visualizations** | `planned` | 4 | Sparklines with a textual reading attached. Score history already reaches team pages; the Performance table is now the midseason lead section and is the obvious place for one. |

---

## Tier 4 — later

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 12 | **Mobile quick capture** | `planned` | remote auth | Under 15 seconds from phone to a Commissioner Note attached to a team/matchup. Browser speech-to-text only; no custom voice service. |
| 13 | **Polls / reactions** | `planned` | none | Giscus needs GitHub accounts. Needs an abuse-resistant model with no custom account system. |
| 14 | **Draft "how it aged"** | `planned` | 6 | REACH/STEAL stays immutable market analysis; the ageing is a separate, additive layer. |
| 15 | **Universal search / command palette** | `deferred` | none | Useful as the Desk grows. Must not become a frontend framework. |

---

## Merges and drops recommended

- **#3 into #1.** Significance is not a separate surface. It shipped as a
  module (`significance.py`) that the inbox consumes, and it is reusable by
  anything else that needs to rank candidates.
- **The weekly `/stories` Story Board into the Change Inbox.** Two pages now
  rank the same candidate stream with different models. Keep the Story Board
  for the draft issue, where there is no sync-to-sync diff.
- **#8's "auto status" stays advisory.** A take is never marked wrong by the
  engine; it proposes and the commissioner decides. Small samples lie.
- **#5 must not ship before week 6.** `playoff_outlook` already refuses to
  print percentages early, and that restraint is the feature.

## Standing constraints

- Nothing reaches `dist/` without commissioner approval and publication.
- Significance diagnostics, ignored items, notes, briefs, evidence and internal
  confidence are private. `audit_output()` enforces it; keep the tests.
- Every Tier 1 Commissioner surface has to work on a phone.
- Sync must not feel slow. Timings are recorded per step on the sync job.
- Ranking tests use synthetic scenarios; no test depends on live results.
