# Deploying the public site

**LIVE: https://league-page-ten-sandy.vercel.app** — Vercel project
`league-page` (account biomusician, scope "biomusician's projects"),
deployed 2026-08-29. Deploy unit is `dist/` only; the CLI is authenticated
on this machine (`npx vercel whoami`).

The public artifact is fully static: no server code, no database, no API
keys, no Sleeper calls at page-view time.

## Deploy / redeploy to Vercel

```
cd dist
npx vercel link --yes --project league-page
npx vercel deploy --prod --yes
```

The `link` step is needed after every rebuild because `build_public_site.py`
wipes `dist/` (including the `.vercel/` link metadata Vercel writes there).
It is non-interactive and idempotent. The CLI never uploads `.vercel/` or
`.env.local`. Preview deploy: drop `--prod` (preview URLs sit behind
Vercel's deployment protection; production is public).

After deploying, spot-check: `/`, `/disco/`, `/surfeit/`, a draft page, an
archive issue, and confirm `/CLAUDE.md` and `/editorial/managers.json`
return 404.

## Build

```
.venv\Scripts\python.exe scripts\build_public_site.py
```

- Output: `dist/` (gitignored; regenerate any time).
- The build validates publication gates and then audits its own output for
  private material (markers, evidence IDs, desk artifacts, local paths,
  Sleeper handles outside the verbatim historical archive). A non-empty
  audit FAILS the build; never deploy a failed build.
- `--preview <league>:<issue_key>` renders one unpublished issue with a
  commissioner-preview banner into `dist-preview/` instead. **Never deploy
  dist-preview/.**

## Local preview

```
.venv\Scripts\python.exe -m http.server 8777 --bind 127.0.0.1 --directory dist
```

then open http://127.0.0.1:8777/ .

## Environment requirements

- This machine's `.venv` (Python + jinja2 + markdown); nothing else.
- The local DB (`data/league.sqlite3`) and `published/` snapshots are build
  INPUTS only; nothing from `data/`, `editorial/`, or `.claude/` is copied out.

## Routing

Every page is `<path>/index.html`, so clean URLs (`/surfeit/2026/week-01/`)
work on any host that serves directory indexes (all of the above do). No
rewrites, no server config required. Issue permalinks are stable; new issues
never move old ones.

## Publishing a new issue (the whole cycle)

1. Double-click **`Launch Commissioner Desk.cmd`** (repo root, or the
   desktop shortcut). Browser opens at http://localhost:8026/commissioner.
2. Desk: issue workspace → Build → make decisions; Claude Code works
   `editorial/<season>/<league>/<issue>/AUTHORING_INDEX.md` with the
   my-writing-style skill.
3. Click **EDIT ISSUE**: edit every section inline, approve, fix the
   listed blockers.
4. **Publish… → Publish & Deploy**: freezes the snapshot, rebuilds the
   audited `dist/`, deploys to Vercel production, and verifies the live
   URLs. (Publish Locally does everything except deploy; the manual CLI
   commands above remain the troubleshooting fallback.)
5. Commit the new `published/` snapshot.

Sync first if the data is stale: `.venv\Scripts\python.exe scripts\sync.py`.

Republishing an old issue is deliberate: re-run PUBLISH on that issue;
normal rebuilds never touch frozen snapshots.

## What must never be deployed

`data/`, `editorial/`, `published/` sources (only their rendered pages ship),
`.claude/`, `templates/`, `leaguepage/`, `scripts/`, `dist-preview/`, and the
private history bundle. The build never copies them; the audit double-checks.
