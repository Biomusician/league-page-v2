# Deploying the public site

The public artifact is fully static: no server code, no database, no API
keys, no Sleeper calls at page-view time. Any static host works (GitHub
Pages, Cloudflare Pages, Netlify, an S3 bucket, a USB stick).

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

1. `scripts\sync.py`
2. Desk (`scripts\desk.py`) → issue workspace → Build → make decisions
   (Review Packet shows everything on one screen)
3. Claude Code: work `editorial/<season>/<league>/<issue>/AUTHORING_INDEX.md`
   with the my-writing-style skill
4. Desk: edit, approve modules, PUBLISH (freezes a snapshot under
   `published/` — commit it)
5. `scripts\build_public_site.py`
6. Upload `dist/` to the host (or push, once a deployment destination is
   approved — none is configured yet, and nothing here pushes anywhere).

Republishing an old issue is deliberate: re-run PUBLISH on that issue;
normal rebuilds never touch frozen snapshots.

## What must never be deployed

`data/`, `editorial/`, `published/` sources (only their rendered pages ship),
`.claude/`, `templates/`, `leaguepage/`, `scripts/`, `dist-preview/`, and the
private history bundle. The build never copies them; the audit double-checks.
