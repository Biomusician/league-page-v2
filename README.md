# League Page

A league "newspaper" for two Sleeper fantasy football leagues — **Disco Chat** and
**The Surfeit** — with a private Commissioner's Desk for authoring each weekly issue
and a static public site for reading them.

- Full product spec: [docs/SPEC.md](docs/SPEC.md)
- Architecture decisions: [docs/DECISIONS.md](docs/DECISIONS.md)
- Current state / next steps: [docs/HANDOFF.md](docs/HANDOFF.md)
- Working conventions: [CLAUDE.md](CLAUDE.md)

## Architecture at a glance

| Layer | Where it lives |
|---|---|
| Private source | GitHub private repo `Biomusician/league-page-v2` |
| Private authoring | Commissioner's Desk, runs only on this machine; its runtime state (`data/`, `logs/`, `editorial/managers.json`) never enters git |
| Published data | Sanitized immutable issue snapshots under `published/`, git-tracked |
| Public build | `scripts/build_public_site.py` renders `dist/` and fails on any privacy-audit finding |
| Deployment | Vercel production (`league-page` project) serves the `site` branch, which carries only the audited `dist/` output — see [docs/DEPLOY.md](docs/DEPLOY.md) |

## Open the Commissioner's Desk

**Double-click `Launch Commissioner Desk.cmd` in this folder** (or the
"League Commissioner Desk" desktop shortcut). It starts the private server,
waits until it is healthy, and opens your browser at
http://localhost:8026/commissioner. Keep the terminal window open while you
work; closing it stops the Desk. If 8026 is held by another program, the
launcher picks a nearby free port and opens that instead; if a Desk is
already running, it just opens the browser. Startup log:
`logs/desk-startup.log`.

Troubleshooting fallback (what the launcher runs for you):

```
.venv\Scripts\python.exe scripts\desk.py
```

## The weekly workflow (all in the browser)

Every Tuesday at noon a cloud routine ("League-Page Tuesday prep",
https://claude.ai/code/routines) posts a research pack: last week's
post-mortem, next week's matchups, a per-team news sweep, award
candidates, and the reminder that writing is due Wednesday evening. It
runs in the cloud on public data only — the steps below are the local
half, and the Desk's ghost briefs stay authoritative.

1. Double-click **`Launch Commissioner Desk`**
2. Click **SYNC SLEEPER** (pulls both leagues, records snapshots and move
   context, and refreshes the writing briefs for the current week's
   workspaces — no separate Build step)
3. Click **EDIT WEEK N**
4. Write
5. Preview / approve
6. **Publish & Deploy**

No terminal needed. The CLI below is troubleshooting fallback only.

## Back up your writing

```
.venv\Scripts\python.exe scripts\export_commissioner_state.py
```

Writes a complete, checksummed bundle of everything you have written and
decided (prose, approvals, revisions, team-name overrides, rankings,
story/award decisions, manager context) to `backups/` — gitignored,
because it contains private material. To prove a backup is good, restore
it somewhere harmless and diff the checksum:

```
.venv\Scripts\python.exe scripts\import_commissioner_state.py backups\<file>.json --dry-run
```

Restoring over the live store needs `--force`; restoring into a scratch
`--db` / `--editorial` needs nothing and is the safe way to verify.

## Troubleshooting / data CLI

```
.venv\Scripts\python.exe scripts\sync.py            # same sync the Desk button runs
.venv\Scripts\python.exe scripts\import_archive.py  # index archive/*.md into the DB
.venv\Scripts\python.exe scripts\seed_editorial.py  # refresh editorial/managers.json from synced data
.venv\Scripts\python.exe -m pytest tests\ -q        # run the test suite
```

## Editing an issue

From the Desk home, click **EDIT DRAFT ISSUE** (or **EDIT WEEK N**). The
Issue Editor is one screen for the whole issue, and every empty section
starts as a **ghost writing brief**: subdued private suggestions (strongest
facts, story angles, callbacks, a structure) rendered inside the editor.
Your first keystroke replaces the ghost; deleting everything brings it
back; the same material stays available under **Writing brief** and **Show
evidence** below each editor. Ghost text is never content: it cannot save,
publish, or count as written. Cards autosave with per-section
approve/preview, revision History with restore, and a team-name panel.
Briefs recompute from the latest synced data at page load; a "data updated
since written" chip appears when a sync postdates your prose.

Want Claude to write instead? **Request Claude draft** (empty section) or
**Request rewrite** (existing text) queues a note; then tell Claude Code:

> Work all pending rewrite requests in
> editorial/<season>/<league>/<issue>/REVISION_REQUESTS.md.

Claude writes `proposals/<section>.md`; the editor shows it beside your
current text with Accept / Keep — your text is never replaced silently.
**Publish…** shows the exact blockers (or READY), then offers Publish
Locally or Publish & Deploy (build + privacy audit + Vercel production,
never past a failed audit).

Recurring sidebar features (currently **The All-City Team** and its
100,000-population variant **The All-Marquee Team**) are opt-in modules
backed by a git-tracked data edition. Include the module on the issue, and
the table, rule footnote and near-miss list render from
`editorial/features/<feature>/<edition>.json` while you write the copy in
the ordinary section card. Both can run in the same issue. See
`editorial/features/all-city/README.md` for the rule and the procedure for
running one again later in the season.

## Weekly issue workflow (per league)

1. Double-click `Launch Commissioner Desk.cmd`.
2. On the Desk: **Build** in the issue workspace (packets + briefs), make
   story/award decisions, then ask Claude Code:

> Work the task list in editorial/<season>/<league>/week-NN/AUTHORING_INDEX.md
> using my writing-style skill.

3. Click **EDIT WEEK N**: edit, approve, Publish & Deploy.

## Matchup Lab detail (per league)

```
.venv\Scripts\python.exe scripts\sync.py
.venv\Scripts\python.exe scripts\build_weekly_packet.py --league surfeit --week 1
.venv\Scripts\python.exe scripts\desk.py
```

On the Desk (`/commissioner/<league>/<season>/week/<N>/matchups`): pick angles,
add notes, override prominence. Rebuild the packet so decisions flow in, then in
a Claude Code session ask:

> Draft all unapproved matchup previews for surfeit week 1 using my
> writing-style skill. Follow each matchup's generated/AUTHORING.md.

Edit each draft on the Desk (remove the ROUGH DRAFT marker), approve or send
revision requests, then publish the week page:

```
.venv\Scripts\python.exe scripts\publish_week.py --league surfeit --week 1
```

## Draft workflow (per league)

```
.venv\Scripts\python.exe scripts\sync.py
.venv\Scripts\python.exe scripts\build_editorial_packet.py --league surfeit --type draft
```

Review candidates/awards on the Desk, rebuild the packet (decisions flow in),
then have a Claude Code session author `draft-issue.md` per the packet's
`AUTHORING_BRIEF.md`. Edit it, save as `issue.md` (remove the ROUGH DRAFT
marker), then:

```
.venv\Scripts\python.exe scripts\publish_issue.py --league surfeit --issue draft --approve
.venv\Scripts\python.exe scripts\publish_issue.py --league surfeit --issue draft --publish
```

No API keys are required. Claude Code (this repo's editorial AI environment) authors
prose from generated editorial packets; the deployed site is static and needs no LLM.
