# League Page

A league "newspaper" for two Sleeper fantasy football leagues — **Disco Chat** and
**The Surfeit** — with a private Commissioner's Desk for authoring each weekly issue
and a static public site for reading them.

- Full product spec: [docs/SPEC.md](docs/SPEC.md)
- Architecture decisions: [docs/DECISIONS.md](docs/DECISIONS.md)
- Current state / next steps: [docs/HANDOFF.md](docs/HANDOFF.md)
- Working conventions: [CLAUDE.md](CLAUDE.md)

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

## Quick start (data + tests)

```
.venv\Scripts\python.exe scripts\sync.py            # pull Sleeper data for both leagues
.venv\Scripts\python.exe scripts\import_archive.py  # index archive/*.md into the DB
.venv\Scripts\python.exe scripts\seed_editorial.py  # refresh editorial/managers.json from synced data
.venv\Scripts\python.exe -m pytest tests\ -q        # run the test suite
```

## Editing an issue

From the Desk home, click **EDIT DRAFT ISSUE** (or **EDIT WEEK N**). The
Issue Editor is one screen for the whole issue: every included section is an
editable card (capsules split per team) with autosave, per-section
approve/preview, revision History with restore, and a team-name panel.
**Request rewrite** on a card queues a note for Claude Code; then tell
Claude Code:

> Work all pending rewrite requests in
> editorial/<season>/<league>/<issue>/REVISION_REQUESTS.md.

Claude writes `proposals/<section>.md`; the editor shows it beside your
current text with Accept / Keep — your text is never replaced silently.
**Publish…** shows the exact blockers (or READY), then offers Publish
Locally or Publish & Deploy (build + privacy audit + Vercel production,
never past a failed audit).

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
