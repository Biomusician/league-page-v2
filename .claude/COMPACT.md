# Post-compaction context re-injection (League Page)

This file is injected into Claude's context automatically after every context
compaction (SessionStart hook, matcher "compact"). It restates the invariants
that must never be lost to summarization. The compaction summary supplements
this; where they conflict, the repo files win.

## Project

League Page — two-league fantasy publication system at
`C:\Users\Jonathan\League-Page` (the session cwd may be `C:\Users\Jonathan\
Fantasy Bot`; League Page work always targets the League-Page repo).
Leagues: Disco Chat (12-team superflex half-PPR, CRC theme) and The Surfeit
(10-team 1QB half-PPR, Force Design/Futures theme).

Read before resuming substantive work:
- `docs/HANDOFF.md` — current state (authoritative)
- `docs/DECISIONS.md`, `docs/SPEC.md`, `docs/DEPLOY.md`
- `CLAUDE.md` in the repo root
- `.claude/skills/my-writing-style/SKILL.md` — the ONLY voice authority.
  Installed verbatim from Jonathan. Never regenerate, update, or "learn"
  style rules from AI-generated prose.

## Current priority (Jonathan, 2026-08-29)

Ship the public MVP to Vercel production today. Fable is authorized for
this tranche and for MVP/maintenance work. Production deployment of the
AUDITED PUBLIC ARTIFACT (dist/) to Vercel is authorized, including project
creation and preview/production deploys. Launch-editorial publishing for
the MVP was authorized. Private Commissioner/editorial material must
remain strictly local.

## Hard prohibitions (standing, never cleared by compaction)

1. Deploy ONLY the audited public artifact (dist/). Never deploy or expose
   the authoring repo, data/, editorial/, .claude/, published/ sources,
   the Desk, tests, or the private history bundle. Do not make the source
   repository public or push it to a remote without explicit approval.
2. Outside the explicitly authorized MVP launch content, never mark
   commissioner approval, publish an issue, or remove ROUGH DRAFT markers
   on Jonathan's behalf. Generated prose never auto-publishes.
3. No Anthropic API / LLM API anywhere in the product. Claude Code IS the
   editorial AI.
4. Privacy: real Sleeper handles live only in local gitignored
   `editorial/managers.json` and the DB. Never commit or publish them;
   public fallback slugs are `roster-{N}`. The private history bundle
   (`League-Page-PRIVATE-history-backup-2026-08-29.bundle`) is never pushed
   or reimported.
5. Never hardcode league settings the Sleeper API reports at runtime.
6. Don't hammer free sources; Sleeper players endpoint is cached ~20h.

## Gates and confirmed facts

- **Model gate**: superseded for MVP/maintenance work (Jonathan,
  2026-08-29): Fable is authorized unless a later task explicitly requests
  Opus.
- CONFIRMED (Jonathan, 2026-08-29): FRA/UK = Surfeit roster 8 (L'entente
  Discordiale); JPN/SWE = Surfeit roster 7 (Wild SeeKats).
- UNVERIFIED (never usable as fact): chrys*** = "EMCO".
- Voice bans include: em-dashes, the negated-parallel family, "load-bearing",
  cross-league gag leakage. Full list in the skill; `style_check.py` is
  warnings-only, the skill-level sweep is authoritative.

## Mechanics

- Python: always `.venv\Scripts\python.exe` (three Pythons on PATH); always
  `encoding="utf-8"` when writing files from Python.
- Tests: `.venv\Scripts\python.exe -m pytest tests/ -q` in League-Page
  (117 passing, network-free) — run them before claiming work done.
- Commit locally at checkpoints without asking; pushing is a separate,
  gated decision.
- Compaction standing instruction from Jonathan (2026-08-29): auto-compact
  fires at 800K tokens of Fable 5's native 1M context window
  (`autoCompactWindow: 800000` in user settings; interactive equivalent:
  `/autocompact 800k`, Claude Code v2.1.221+). After any compaction,
  re-read this file's pointers before resuming.
- Two complementary mechanisms: `/compact <instructions>` steers the
  compaction summary itself; this file is re-injected into context
  afterward by a SessionStart hook with matcher "compact" regardless of
  trigger. (Verified on Claude Code 2.1.247: SessionStart/compact is the
  documented post-compaction injection point; PostCompact hooks exist but
  their stdout is not added to context.)

## When summarizing (manual /compact guidance)

Preserve verbatim: the model-gate status and any pending phase tranche; every
prohibition above; the list of decisions currently waiting on Jonathan;
unresolved public team names. Compress aggressively: tool output, file dumps,
draft prose already saved to disk (the repo copy is the source of truth).
