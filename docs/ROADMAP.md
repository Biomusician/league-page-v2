# Roadmap

Ranked product roadmap. **Future work only** — what exists today is in
`docs/HANDOFF.md`, and why it was built that way is in `docs/DECISIONS.md`.
Keep those three separate: HANDOFF gets stale if it doubles as a wish list.

Reviewed 2026-09-01.

## The product rule

Sleeper already gives the league rosters, standings, scores, transactions and
player lists. League Page earns its place by answering what Sleeper does not:
what changed, why it matters, what is unusual, what to watch, what history
says, and what the Commissioner should consider writing about. A feature that
mainly reproduces Sleeper gets rethought or dropped.

Private side, the equivalent test: does this remove research, navigation, or
repetitive judgment from the weekly process? If not, it is not Tier 1.

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
3. **Durable jobs table** — replaces the `_JOB`/`_JOBS` process globals.
4. **Filesystem write sites** — 47 across the Desk, rejected by a read-only
   serverless runtime.
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

---

## Tier 2 — the consumer half

Do not start until Tier 1 has been used in a real weekly cycle.

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 4 | **Personalized "Your Team"** | `partial` | none | Team pages already carry outlook, strengths, form, key moves and positional profile; what is missing is the interpreted briefing at the top. Mostly assembly. |
| 5 | **Playoff scenario explorer** | `partial` | 4 | `team_analytics.playoff_outlook` already produces bands and percentages with an honest too-early stage. Missing: per-game leverage and rooting interest. |
| 6 | **Transaction decision ledger** | `partial` | none | `transaction_analysis` already snapshots rationale and tracks outcome and rank shift. Missing: the durable at-the-time/how-it-aged split as a public surface. |
| 7 | **Matchup forecast + upset path** | `partial` | 5 | Structural edge, vulnerability, form and receipts all exist. Missing: the upset-path narrative. |

Recommended order within Tier 2: **6 → 4 → 7 → 5.** The ledger is closest to
done and produces newsletter material immediately; the scenario explorer is
the most work and the least useful before week 6.

---

## Tier 3 — depth

| # | Feature | Status | Depends on | Rationale |
| --- | --- | --- | --- | --- |
| 8 | **Receipts / takes tracker** | `partial` | 1 | The `takes` table already carries status open/validated/contradicted/retired/too_early, and the inbox already surfaces a receipt when new evidence exists. Missing: the tracker view and auto-status proposals. |
| 9 | **Smart archive recall** | `partial` | none | `story_memory.retrieve_callbacks` already ranks by FTS relevance, dating confidence, prior reuse and league scope. Better than the "broad substring" the roadmap assumed. Missing: retrieval from arbitrary sections, not only matchups. |
| 10 | **Consumer email digest** | `planned` | remote auth, Resend | Separate data model from Commissioner mail. Never send whole issues; the site stays canonical. |
| 11 | **Trend visualizations** | `planned` | 4 | Sparklines with a textual reading attached. Score history already reaches team pages. |

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
