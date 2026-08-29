# HANDOFF

Updated 2026-08-29, end of the MVP-to-Vercel tranche. Companions:
docs/SPEC.md (product spec), docs/DECISIONS.md, docs/DEPLOY.md (deploy
playbook), POST_MVP.md (backlog).

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
- Logos (from Jonathan, 2026-08-29): static/disco-logo-banner.jpg (dark
  masthead + root card), static/disco-logo-light.png (unused, kept for
  light contexts), static/surfeit-logo.jpg (Skunk Works badge; masthead +
  root card). build_site copies static/ -> dist/assets/.

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

## Voice (authoritative)

.claude/skills/my-writing-style/SKILL.md — supplied by Jonathan, installed
verbatim, never regenerate. Weekly/draft authoring workflows are explicit
drafting requests (drafting override). style_check.py is warnings-only;
the skill-level sweep is authoritative.

## Top post-MVP tasks

See POST_MVP.md. Short version: real names for the 8 neutral rosters,
finish the Draft Issues, preseason Peer and Near-Peer, preseason Takes,
custom domain + one-command deploy.
