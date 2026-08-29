# Archive style notes — SECONDARY reference

> **`.claude/skills/my-writing-style/SKILL.md` controls.** That skill, supplied
> by Jonathan, is the single authoritative voice profile. This file is only
> supporting observations from the imported 2019–2025 newsletter archive:
> exemplar pointers and league-specific theming detail the skill does not
> cover. Where anything here differs from the skill, the skill wins. Do not
> merge this file into the skill or update the skill from it.

## Exemplar issues (read for texture, newest voice first)

- `archive/disco/2025-week-07.md` — current voice, full power-ranking issue
- `archive/disco/2023-week-06.md` — "hate/love/miss/hit" per-team format
- `archive/disco/2021-week-04.md` — matchup-preview format with theme device
- `archive/daddy/2019-week-12.md` — early voice: playoff-race math + GM interview

## Formats observed in the archive (structural, not voice)

- Power-ranking team blocks open with a header block (Record / Strengths /
  Weakness / Forecast), then free prose. Forecast values are blunt buckets:
  "Playoff lock", "Toilet bowl contender".
- Matchup previews carry explicit odds ("53/47", "70/30") and a designation
  line ("Match of the Week", "Toilet Bowl", "Upset Special").
- Recurring sections: Seasons Past (champions/losers list), Hall of Fame /
  Hall of Shame (score records with week+season), Side Bet Status, Rookies of
  the Week, Week's Worst Decision, GM of the Week interviews.
- Issue numbering: "Vol N.RomanWeek" (e.g. Vol 6.II).

## League theming (project-specific, per Jonathan)

**Disco Chat — Control and Reporting Center / operational battle management.**
Air-picture language used correctly and unglossed: tracks, the common tactical
picture, commit criteria, clean/dirty picture, datalink, the ATO. A matchup is
a "track of interest"; a chaotic scoreboard is a "dirty picture."

**The Surfeit — Force Design / Futures, ~2035.** Real force-design concepts
applied substantially correctly: Agile Combat Employment, distributed
operations, contested logistics, multi-capable Airmen, collaborative combat
aircraft, kill chains/kill webs, mission command, attritable assets. The humor
comes from accurately applying institutional language to fantasy football,
never from random Pentagon-buzzword soup. Branches and Sequels / False
Assumptions are planning-language features and get a straight face.

Canonical section labels (both leagues): Common Tactical Picture, Peer and
Near-Peer Competition, Tracks of Interest, Fades, Force Flow, Black Box,
Intel Prep of the Fantasy Space, Shame! Shame! Shame!. Surfeit adds Branches
and Sequels, False Assumptions.

## Coalition humor lanes (per Jonathan, see editorial/coalitions.json)

Rotate angles rather than repeating one: coalition command relationships,
fighter culture, Rafale vs Gripen, operator-vs-maintainer dynamics,
procurement/acquisition comparisons, alliance politics, interoperability,
capability jokes grounded in recognizable facts, pilots creating work for
maintenance, multinational command dysfunction. The editorial-usage log
(`storage.editorial_usage`) tracks which lanes ran recently.

## Hard rules restated (enforced elsewhere, listed for convenience)

- Facts come from packets; unverified metadata never appears as fact.
- In-jokes stay scoped to their source league unless Jonathan marks one
  cross-league.
- The roast targets rosters, decisions, transactions, predictions, outcomes,
  processes, and organizational absurdity. It does not demean the person.
- Sweep league prose for em-dashes and the negated-parallel contrast family
  before calling it done (see the skill; `scripts/style_check.py` catches the
  mechanical cases).
