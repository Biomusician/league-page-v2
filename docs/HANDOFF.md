# HANDOFF

Updated 2026-08-29 (end of Phase 4.5 + Phase 5 Matchup Lab tranche). Read
docs/SPEC.md (product spec) and docs/DECISIONS.md (architecture) first.

## Voice (authoritative)

`.claude/skills/my-writing-style/SKILL.md` — supplied by Jonathan, installed
verbatim, never regenerate from the archive. CLAUDE.md carries the always-on
pointer and the drafting-override (weekly/draft workflows are explicit
drafting requests). `editorial/style/ARCHIVE_STYLE_NOTES.md` is secondary.

## Matchup Lab (Phase 5 — built and dry-run against real Surfeit Week 1)

The weekly loop:

1. `scripts/sync.py`
2. `scripts/build_weekly_packet.py --league <slug> --week <N>`
3. Desk (`scripts/desk.py`) → `/commissioner/<league>/<season>/week/<N>/matchups`:
   pick angles (5 rule-generated families per matchup), notes, prominence
   overrides. Two-axis interest (Competitive Importance / Story Value) shows
   its components; weights adjustable in `matchup_interest.py`.
4. Rebuild the packet (decisions + revision requests flow into AUTHORING.md).
5. Claude Code: "Draft all unapproved matchup previews for <league> week <N>
   using my writing-style skill." Drafts land at
   `editorial/<season>/<league>/week-NN/matchups/<slug>/draft.md` with the
   ROUGH DRAFT marker and a usage comment.
6. Desk: edit (marker must go), approve/lock (blocked while marker present;
   approval parses the usage comment into the repetition log), or send
   structured revision requests (requeues for the next Claude Code pass).
7. `scripts/publish_week.py --league <slug> --week <N>` → public Common
   Tactical Picture page; only approved/locked drafts render.

Story Memory is league-scoped: cross-league callbacks only for managers with
`allow_cross_league_callbacks: true` in local managers.json. Repetition
control: `editorial_usage` table + collision warnings on angles; coalition
joke lanes rotate. Five TEST drafts (marked, unapproved) sit in
`editorial/2026/surfeit/week-01/` from the authoring dry run.

## Privacy

Real Sleeper handles live only in local (gitignored) `editorial/managers.json`
and the local DB; committed files use team names/nicknames. **Git history
before 2026-08-29 still contains the removed files — resolve before any push**
(fresh-history publish or filter). Surfeit has five unnamed rosters; the test
drafts use provisional labels. Getting real nicknames into managers.json
aliases is the fix.

## Architecture (corrected this tranche)

    Sleeper / archive / metadata → deterministic analytics → structured
    editorial context (packets) → Claude Code authoring/editing →
    git-tracked published issue

Claude Code IS the editorial AI. No LLM API keys anywhere; a one-click API
authoring feature is explicitly post-V1. Generated prose never auto-publishes.

## What exists and works

- **Ingestion** (`scripts/sync.py`): both leagues' settings/rosters/users/
  matchups/transactions/drafts in `data/league.sqlite3`. Preseason-aware.
- **Archive**: 55 issues (2019–2025) in `archive/`, FTS-indexed, with full
  source provenance (`archive/provenance.json`) and a spot-check report
  (`scripts/audit_archive_dating.py` — 18 flagged rows, all explained; the
  one genuinely ambiguous doc is `disco/2025-week-05.md`).
- **Editorial metadata** (`editorial/*.json`): confirmed/inferred/rejected
  statuses. FRA/UK/JPN/SWE coalition identities recorded as confirmed facts;
  their roster mappings are INFERRED (evidence noted) and unusable in copy
  until Jonathan confirms. The "EMCO" manager-alias inference remains
  unverified (details in local managers.json).
- **Reference ranks** (`refdata/adp/`): FantasyPros ECR snapshots (half-PPR
  for Surfeit, superflex for Disco, retrieved 2026-08-29) behind an
  ADP-source abstraction; `scripts/import_adp.py` refreshes or imports any
  CSV. Missing players → no delta, never fabricated.
- **Draft analytics** (`leaguepage/draft_analysis.py`): deterministic facts
  with evidence IDs (`leaguepage/evidence.py` — scheme shared with future
  Matchup Lab). Verified on Surfeit's real 150-pick draft (0 unmatched
  players) and on synthetic 10/12-team, partial, and empty drafts.
- **Story candidates** (`draft_stories.py`) + **award nominations**
  (`draft_awards.py`): scored/ranked, evidence-backed, never auto-decided.
- **Commissioner's Desk** (`scripts/desk.py`, localhost:8026): draft-review
  screen — Story Board (Include/Save/Ignore + notes), award decisions,
  preseason Peer and Near-Peer Competition (commissioner-owned), Track-as-
  Take with verdict recording. All decisions persist in SQLite.
- **Editorial packets** (`scripts/build_editorial_packet.py --league X
  --type draft`): self-contained authoring context under
  `editorial/<season>/<league>/draft/generated/` incl. AUTHORING_BRIEF.md,
  dossiers, allowed-callback archive context, confirmed-only manager context
  with BANNED list. Deterministic/idempotent except MANIFEST.json.
- **Style profile** (`editorial/style/STYLE_PROFILE.md`): the Daddy/Disco
  voice distilled with exemplar pointers and anti-patterns.
- **Publishing skeleton** (`scripts/publish_issue.py`): generated → edited →
  approved → published; ROUGH DRAFT marker + explicit approval both block.
- **Tests**: 47, synthetic, no network.

## The intended weekly/draft loop

1. `scripts/sync.py`
2. `scripts/build_editorial_packet.py --league <slug> --type draft`
3. Desk (`scripts/desk.py`) → decide stories/awards, set rankings, track takes
4. Rebuild the packet (decisions flow into it)
5. Claude Code session: read `generated/AUTHORING_BRIEF.md`, write
   `editorial/<season>/<league>/draft/draft-issue.md`
6. Jonathan edits → saves as `issue.md` without the marker
7. `scripts/publish_issue.py --approve` then `--publish` → `site/...html`

## Waiting on Disco's draft (~Aug 30)

After it completes, run exactly:

    .venv\Scripts\python.exe scripts\sync.py
    .venv\Scripts\python.exe scripts\build_editorial_packet.py --league disco --type draft

Same pipeline, no code changes expected (12-team format is tested).

## Needs Jonathan

- Confirm or reject: EMCO alias; FRA/UK ↔ Surfeit roster 8 ("L'entente
  Discordiale"); JPN/SWE ↔ Surfeit roster 7 ("Wild SeeKats"). Flip
  `status` to `confirmed`/`rejected` in editorial/*.json.
- Map FRA/UK/JPN/SWE identities to manager keys in coalitions.json
  (`sleeper_manager` fields) when ready.
- Fill recurring bits / sensitivity flags in managers.json as desired.
- Preseason Peer and Near-Peer Competition on the Desk (becomes receipts).
- Hosting decision executes at first publish (GitHub Pages agreed; pushing
  needs explicit approval).

## Next build phase (not started)

Weekly Awards Board (nomination engine over weekly results — schema and
evidence scheme ready), Lowdown Prep, full Issue Builder integration of
approved matchup previews, richer public pages, Intel Prep / Branches and
Sequels / False Assumptions (late-season). Projections remain unavailable
(Sleeper's public API has none); a projection source would activate the
projection-closeness scoring and Photo Finish tagging already in place.

## Gotchas

- Three Pythons on PATH — always `.venv\Scripts\python.exe`.
- Always pass `encoding="utf-8"` when writing files from Python here.
- Sleeper players endpoint cached 20h; don't force-refetch.
- `data/` and `site/` are gitignored; `refdata/`, `archive/`, `editorial/`
  are tracked and load-bearing for provenance.
