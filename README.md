# League Page

A league "newspaper" for two Sleeper fantasy football leagues — **Disco Chat** and
**The Surfeit** — with a private Commissioner's Desk for authoring each weekly issue
and a static public site for reading them.

- Full product spec: [docs/SPEC.md](docs/SPEC.md)
- Architecture decisions: [docs/DECISIONS.md](docs/DECISIONS.md)
- Current state / next steps: [docs/HANDOFF.md](docs/HANDOFF.md)
- Working conventions: [CLAUDE.md](CLAUDE.md)

## Quick start

```
.venv\Scripts\python.exe scripts\sync.py            # pull Sleeper data for both leagues
.venv\Scripts\python.exe scripts\import_archive.py  # index archive/*.md into the DB
.venv\Scripts\python.exe scripts\seed_editorial.py  # refresh editorial/managers.json from synced data
.venv\Scripts\python.exe scripts\desk.py            # Commissioner's Desk at http://127.0.0.1:8026/commissioner
.venv\Scripts\python.exe -m pytest tests\ -q        # run the test suite
```

## Weekly Matchup Lab workflow (per league)

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
