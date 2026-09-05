# League Page

One application, two league identities: **Disco Chat** (`/disco`) and **The Surfeit**
(`/surfeit`). A weekly league "newspaper" (Lowdown, matchup previews, awards, power
tiers) authored in a private localhost Commissioner's Desk, published as immutable
static HTML. Full product spec lives in docs/SPEC.md; architectural decisions in
docs/DECISIONS.md; current state in docs/HANDOFF.md.

## Run it

```
.venv/Scripts/python.exe scripts/sync.py            # pull Sleeper data (both leagues)
.venv/Scripts/python.exe scripts/import_archive.py  # index archive/*.md newsletters
```

The Commissioner's Desk web app and the static publish pipeline are being built —
see docs/HANDOFF.md for what exists today.

## Test it

```
.venv/Scripts/python.exe -m pytest tests/ -q
```

Tests are fully synthetic — no network. Keep it that way.

## Architecture

- `leaguepage/config.py` — league registry (slug, Sleeper ID, theme). Never hardcode
  a league setting the Sleeper API can report; scoring/rosters/playoffs are read from
  the synced `/league` payload at runtime.
- `leaguepage/sleeper.py` — read-only Sleeper API client (throttled, retrying).
- `leaguepage/storage.py` — one SQLite DB (`data/league.sqlite3`): raw Sleeper payloads
  as JSON blobs + editorial tables (archive_issues with FTS5, takes, bit_usage).
- `leaguepage/ingest.py` — idempotent sync, incl. drafts. Players dict cached 20h.
- `leaguepage/archive.py` — indexes `archive/**/*.md` (frontmatter + filename fallback).
- `archive/` — imported historical newsletters, **checked into git**. This is the
  editorial memory corpus; never delete or regenerate it casually.
- `editorial/` — git-tracked JSON: manager identities, coalitions, recurring bits,
  sensitivity flags. Source of truth for Story Memory identity data (DB holds only
  mutable usage logs and takes). Human-edited; keep it valid JSON.

## Conventions

- Use `.venv/Scripts/python.exe` explicitly — three Pythons on this machine's PATH.
- Flat modules, plain functions, `from __future__ import annotations`, dataclasses
  for records. No class hierarchies.
- Every script is idempotent; re-runs must not refetch what hasn't changed.
- Published issues are immutable. Publishing week N must never alter weeks < N.
- Generated prose must be backed by computed evidence, never invented stats.
- `data/` and `site/` are generated and gitignored. `refdata/` (reference-rank
  snapshots) and `archive/` are git-tracked source data — provenance depends on them.

## Voice — authoritative source

When drafting newsletters, power rankings, matchup previews, commissioner
announcements or rulings, portal UI copy, or any prose published as Jonathan:
first read `.claude/skills/my-writing-style/SKILL.md` (the `my-writing-style`
skill) and follow it. That file is the single authoritative voice profile —
supplied by Jonathan, never to be rewritten, shortened, or regenerated from the
newsletter archive. `editorial/style/ARCHIVE_STYLE_NOTES.md` is secondary
supporting examples only; where they differ, SKILL.md controls. Never update
the skill from Claude-generated prose, forwarded AI text, or other people's
writing — only via its own update procedure on Jonathan's explicit feedback.

Project-specific override, from Jonathan explicitly: the skill's default
preference for red-teaming over unsolicited drafting does NOT apply when he
runs a weekly/draft authoring workflow — those runs are his explicit drafting
request, so generate the full requested prose by default.

## Compaction

When context passes 75%, finish the current answer, then compact — never
mid-task. `compact.md` at the repository root says what to preserve
verbatim, what to compress, and what must never appear in a summary.
`.claude/COMPACT.md` is its counterpart: a SessionStart hook re-injects it
after every compaction. Re-read `docs/HANDOFF.md` and re-check `git status`
afterwards rather than trusting a summary's copy of them.

## Editorial AI model

Claude Code IS the editorial AI. The pipeline is:

    Sleeper / archive / metadata → deterministic analytics → structured editorial
    context (packets) → Claude Code authoring/editing → git-tracked published issue

No LLM API keys anywhere: `scripts/build_editorial_packet.py` emits a self-contained
context directory that a Claude Code session consumes to draft prose, which Jonathan
edits and approves before publishing. Facts come from the packet — Claude finds the
story and writes the prose, never the numbers. A one-click in-app API authoring
feature is explicitly post-V1.

## Don't

- Don't add a network call to the test suite.
- Don't make any workflow require an LLM API key, and never auto-publish
  AI-generated prose — publication always follows commissioner approval.
- Don't present a stat without provenance (which dataset, which pick, which issue).
- Don't use unverified aliases/mappings (see editorial/*.json statuses) as fact.
- Don't delete `data/league.sqlite3` to "start clean" without asking — a resync is
  cheap but draft/matchup history on Sleeper can age out of the API.
