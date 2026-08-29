# League Page 2.0 — Information Architecture & Commissioner's Desk

Jonathan's product spec, received 2026-08-29, recorded verbatim (light formatting
only). The build plan derived from it is in DECISIONS.md and HANDOFF.md.

## 1. Product Model

Build **one application with two league identities**, sharing the same data engine
and authoring system but using different routes, visual themes, terminology,
histories, and editorial memory.

### Primary routes

**Disco**: `/disco`, `/disco/week/1`, `/disco/matchups`, `/disco/standings`,
`/disco/power`, `/disco/teams`, `/disco/team/{slug}`, `/disco/draft`,
`/disco/transactions`, `/disco/black-box`, `/disco/archive`

**Surfeit**: same structure under `/surfeit`.

Each URL is directly bookmarkable. The root `/` is a simple league selector rather
than a third generic league homepage.

## 2. Public Navigation

Identical information architecture, different visual identities and selective
terminology:

- **Home** — current weekly issue / front page
- **Common Tactical Picture** — current matchups and matchup context
- **Standings** — traditional standings plus richer underlying performance
- **Peer and Near-Peer Competition** — power rankings and team tiers
- **Teams** — roster and manager profiles
- **Force Flow** — transactions and consequential roster movement
- **Draft** — draft board, draft recap, historical drafts
- **Black Box** — records, historical performances, anomalies, milestones
- **Archive** — weekly newsletters and prior seasons

No generic NFL news or generic fantasy resources — Sleeper and every fantasy site
already do those things better.

## 3. League Home — `/disco` and `/surfeit`

Not a traditional fantasy dashboard: **the current issue of the league newspaper**.

Above the fold:

- **Masthead** — e.g. "DISCO CHAT / Week 6 — 2026" or "THE SURFEIT / Week 6 — 2026",
  with league record/season week, issue subtitle, previous/next issue navigation,
  league switcher.
- **Hero story** — the latest **Lowdown**, prominently treated, credited to the
  commissioner. First 1–2 paragraphs with Continue Reading.
- **Matchup of the Week** — large secondary card (teams, records, standings,
  projected score, story blurb, link to full preview). Visually competes with the
  Lowdown for prominence.

## 4. Weekly Homepage Sections

Below the hero area:

- **Common Tactical Picture** — cards for every matchup: teams, managers, records,
  projection, matchup designation, short preview, eventual final score. Labels:
  Matchup of the Week, Top Table, Basement Brawl, Coalition Warfare, Rivalry,
  Playoff Elimination, Revenge Game, Major, Standard. Feature matchup is larger.
- **Mission Debrief / Weekly Hardware** — published weekly awards, e.g.
  SHAME! SHAME! SHAME! with expandable evidence (points sacrificed, final margin).
  No requirement to award every category every week.
- **Tracks of Interest** — 2–4 teams worth paying attention to (surging, metrics
  better than record, roster improvement, strange performance, dangerous schedule).
- **Fades** — 1–3 teams moving the wrong direction (declining scoring,
  unsustainable record, injuries, poor lineup management, weak remaining roster,
  repeated close losses).
- **Force Flow** — only significant transactions; never recreate the Sleeper feed.
- **Black Box** — appears only when something is noteworthy (records, anomalies,
  streaks, all-time marks).

## 5. Late-Season Module — Intel Prep of the Fantasy Space

Appears when playoff probabilities become meaningful: playoff probability, likely
seeds, strength of remaining schedule, magic numbers, clinching/elimination
conditions.

**Surfeit subsection: Branches and Sequels** — e.g. "Branch: Team X defeats Team Y.
Sequel: A Team Z loss then moves X to approximately 74% playoff probability."
Generated deterministically wherever possible.

## 6. Surfeit-Specific Editorial Module — False Assumptions

A recurring receipts mechanism. The system stores dated claims made in preseason
previews, draft reviews, Lowdowns, power rankings, matchup predictions, and prior
generated copy — then surfaces what actually occurred. The database retains the
original statement, date, issue, team/player references, and later resolution.

## 7. Common Tactical Picture — `/league/matchups`

Week selector (`← Week 5 | Week 6 | Week 7 →`). Every matchup gets a card: team
names, logos, manager/co-manager names, record, score/projection, starter
summaries, editorial classification, link to full matchup article. Optional
statistical expandable panel: all-play, lineup efficiency, PF/PA rank, recent
form, H2H, playoff leverage. The public interface gets facts; the commissioner
interface gets the story-generation tools.

## 8. Peer and Near-Peer Competition — `/league/power`

Not a duplicate of standings. Tiers rather than fake precision:

- Tier 1 — Peer Competition (legitimate contenders)
- Tier 2 — Near-Peer Competition (capable of challenging)
- Tier 3 — Competitive but Flawed
- Tier 4 — Strategic Reassessment Required

Each team: current rank, previous rank, movement, record, PF, all-play, recent
form, short editorial assessment. **Commissioner overrides supported — the
computer supplies evidence; the commissioner owns the ranking.**

## 9. Team Page — `/league/team/{slug}`

Public profile and long-term league memory: header (team name,
manager/co-managers, nationality flags, logo, record, championships — coalition
manager identity explicit, e.g. FRA/France/Rafale pilot, UK/Chinook pilot,
JPN/maintenance officer, SWE/Gripen pilot); current roster; performance
(standing, PF, all-play, lineup efficiency, weekly history, SoS, luck); **Award
Cabinet** (e.g. "Shame! Shame! Shame! — 3", click-through to every issue);
historical record (finishes, championships, playoffs, all-time record, H2H,
highs/lows); **newsletter history** — every substantial mention links back to the
old issue. That archival linking is extremely valuable.

## 10. Draft — `/league/draft`

Both 2026 leagues start fresh, so this is one of the first fully functional pages.
Current season: visual draft board, picks by team, pick number, position, ADP,
delta from ADP. Team draft summaries: positional allocation, reaches, values,
roster construction, stacks, notable bets, early strengths, vulnerabilities.
Editorial draft review: Draft Crusher, Reach of the Draft, Best Value, Most
Interesting Strategy, Most Aggressive Construction, Most Likely to Age Badly. Any
preseason prediction generated here becomes eligible for future False Assumptions.

## 11. Archive — `/league/archive`

Organized by season (2026: Draft Issue, Week 1, 2, 3…; 2025: imported
newsletters). Search should eventually support manager, team, player, award,
phrase, season, matchup. This is how old Daddy/Disco material becomes **living
editorial memory**, not dead DOCX storage.

## The Commissioner's Desk — `/commissioner` (private)

Should feel like an editorial newsroom, not an admin panel.

### 12. Commissioner Home

First screen answers "What needs my attention?" League cards showing pipeline
state per league (Data / Matchup packets / drafts / Awards / Lowdown / Issue /
Publish status).

### 13. Week Workspace — `/commissioner/{league}/{season}/week-{x}`

Central hub. Status pipeline: DATA → STORIES → MATCHUPS → AWARDS → LOWDOWN →
ISSUE → PUBLISH, each clickable. Overall status (sync, analytics, warnings,
drafts, awards, Lowdown, publication). Suggested story queue with Use / Save for
Later / Ignore / Add Note.

### 14. Data Review

Before Claude writes anything, sanity-check the data: matchups detected, rosters
complete, transactions since cutoff, injuries/byes, historical records loaded,
warnings (projection missing, player not found, co-manager metadata incomplete,
H2H unavailable, unusual roster state). "You should never discover data problems
by finding nonsense in a generated paragraph."

### 15. Story Board — `.../stories`

Editorial triage: columns High Value / Possible / Saved / Rejected. Each card:
story score, tags, evidence bullets, actions (Feature / Include / Ignore / Add
Commissioner Note).

### 16–17. Matchup Lab — `.../matchups`

**The most polished screen in the product.** Lists all matchups with recommended
prominence (FEATURE / MAJOR / STANDARD, changeable). Individual matchup screen:

- **Left column — Facts**: current (records, standings, projections, PF/PA,
  all-play, last three weeks); roster (key players, positional advantages,
  questionable starters, injuries, byes, recent additions); history (all-time
  H2H, last meeting, notable scores); league lore (prior newsletter mentions,
  recurring manager bits, previous awards, old predictions).
- **Center column — Story Angles**: 3–5 genuinely different approaches (e.g.
  Coalition Warfare; Fighter Mafia vs Maintenance Reality; Rafale vs Gripen;
  competitive stakes). Select one, or Generate Different Angles (not merely
  regenerate the same prose).
- **Right column — Commissioner Controls**: Include (coalition joke, H2H,
  mismatch, callback, stakes, player, note) / Avoid (joke used last week, stale
  storyline, subject, stat) / Tone (Straight, Light, Daddy, Savage). "Daddy" is
  the default historical voice.

### 18. Matchup Draft

Generate Preview produces full intended word count: Feature 250–400 words, Major
125–200, Standard 75–125. Direct rewriting supported. Beside each paragraph:
**Evidence** — the factual support underlying the prose (ranks, projection
margin, callback reference, manager metadata). Not hidden reasoning.

### 19. Matchup Revision Actions

Keep Facts New Angle / Make Funnier / More Daddy / Less Savage / Shorten /
Expand / Add Callback / Remove Callback / New Opening / New Closing / Regenerate
Selected Paragraph / **Lock Paragraph** (survives subsequent regenerations —
matters enormously for editing speed).

### 20. Matchup Queue

Approve Matchup returns to the queue (✅ / 🟡 draft exists / ⬜). Process rapidly
in sequence.

### 21. Awards Board — `.../awards`

The application calculates candidates; Claude doesn't choose winners blindly.
E.g. Shame! Shame! Shame!: recommended winner with score, evidence (points
sacrificed, losing margin, outcome-changing), alternates. Actions: Award /
Dismiss / Manual Winner → Generate Award Copy.

### 22. Award Slate

Recommends which awards are worth publishing (strong / weak / no legitimate
candidate). **Default: do not publish awards without a worthy winner.**
Categories freely addable/removable.

### 23. Lowdown Prep — `.../lowdown`

Deliberately different from the matchup writer — the commissioner remains the
primary author. Section A: ranked things worth mentioning. Section B: three
substantially different theme proposals. Section C: suggested outline. Section D:
Generate Rough Lowdown, explicitly marked "ROUGH DRAFT — COMMISSIONER EDIT
REQUIRED". Final published Lowdown is credited to the commissioner.

### 24. Tracks of Interest / Fades Board

System recommends candidates with scores and evidence; select 0–4 Tracks, 0–3
Fades; generate blurbs. A team can occasionally be both — UI should warn.

### 25. Force Flow Board

Only transactions meeting an editorial-interest threshold: high FAAB, starter
immediately added, trade, dropped highly-rostered player, former player facing
old team, transaction affecting matchup projection, later-breakout pickup.

### 26. Black Box Board

Auto-detects league/season records, percentiles, margin/scoring records,
anomalies, streaks. Each: Include / Ignore. No interesting event → the public
section disappears entirely.

### 27. Surfeit — False Assumptions Desk

Searches prior stored assertions; shows candidate claim + current evidence +
confidence (e.g. "Strong contradiction"). Actions: Use as False Assumption /
Still Too Early / Dismiss. Claude should not declare an assumption false after
two weird weeks merely because it can make a joke.

### 28. Surfeit — Branches and Sequels Desk

Late season. Engine calculates plausible playoff branches; commissioner selects
which matter editorially; Claude turns them into prose only after the math is
done.

### 29. Issue Builder — `.../issue`

Newspaper layout editor, not blank text editor. Modules: Masthead, Lowdown, Last
Week, Weekly Hardware, Common Tactical Picture, Tracks of Interest, Fades, Force
Flow, Black Box, Intel Prep, Branches and Sequels, False Assumptions. Reorder,
remove, add custom sections.

### 30. Issue Preview

Desktop/mobile toggle — exactly what league members will see. Warnings: missing
matchup, draft text remains, unsupported statistic, unresolved evidence, repeated
joke, old issue accidentally linked, award without published copy.

### 31. Publication Checklist

Required: Lowdown reviewed, all matchup drafts approved, no unresolved data
warnings, no placeholder text. Recommended: awards reviewed, repetition check,
projections refreshed, spelling/name check. Then PUBLISH → issue becomes
`/{league}/week/{x}` and the league home points to it. **Previous issues never
change unless intentionally republished.**

### 32. Story Memory — `/commissioner/memory`

Per manager: identity (nationality, role, aircraft/job, aliases); recurring bits;
retired bits; recent use log; notable events; sensitivity controls (Fair Game /
Use Sparingly / Do Not Use). Keeps the newsletter savage without becoming
repetitive or annoying.

### 33. Coalition Metadata

Relationships as structured objects, not prose. E.g. Coalition A: FRA + UK
(France, United Kingdom, Rafale, Chinook, pilots); Coalition B: JPN + SWE (Japan,
Sweden, MX, Gripen, operator-maintainer dynamic); relationship: Coalition
Rivalry. Permits matchup scoring and story generation without rediscovering the
joke each week.

### 34. Editorial History / Receipts Database

Any meaningful authored statement can be marked "Track as Take" (draft grade,
prediction, power assertion, breakout call, trade assessment, matchup pick).
Stored with date, author, league, week, subject, original wording, confidence,
eventual outcome. Supports False Assumptions, successful predictions, callbacks,
commissioner self-roasting.

### 35. League Configuration

Identity (name, Sleeper league ID, season, team count, logo, theme). Theme packs:
Disco = Operational/CRC; Surfeit = Force Design/Futures/2035. Canonical shared
terminology: Common Tactical Picture, Peer and Near-Peer Competition, Tracks of
Interest, Fades, Force Flow, Black Box, Intel Prep of the Fantasy Space, Shame!
Shame! Shame!. Surfeit-only: Branches and Sequels, False Assumptions.

### 36. Permissions

Public: read everything published (limited raw analytics). Commissioner: story
recommendations, matchup generator, award nominees, editorial memory, Lowdown
prep, edit issue, publish, league configuration.

### 37. Weekly Commissioner Workflow (target)

1. Open Desk (data already assembled) → 2. Resolve warnings → 3. Story Board →
4. Matchup Lab (review angle → generate → edit → approve; the largest time
savings) → 5. Awards → 6. Lowdown Prep → 7. Issue Builder → 8. Preview →
9. Publish.

### 38. Workflow Status Model

Issue states: DATA READY → EDITORIAL REVIEW → DRAFTING → COMMISSIONER REVIEW →
READY TO PUBLISH → PUBLISHED. Component states: Not Started / Generated / Edited /
Approved / Locked.

### 39. Preseason / Draft Workflow — `/commissioner/{league}/2026/draft-review`

1. ingest draft → 2. compare against ADP → 3. assess roster construction →
4. team draft dossiers → 5. draft awards → 6. Lowdown themes → 7. preseason
power tiers → 8. mark takes for future receipts → 9. Week 1 matchup packets →
10. publish Draft Issue. Real-world dataset to test the whole editorial
architecture before Week 1.

### 40. V1 Priorities

Resist building every public page first. V1 must nail: Sleeper ingestion (both
leagues), league/team/manager metadata, draft ingestion, historical newsletter
archive, Story Memory, deterministic weekly analytics, Matchup Lab, Awards Board,
Lowdown Prep, Issue Builder, publishing, public weekly issue, basic
teams/standings/archive pages. V1.5: richer Black Box, all-play visualizations,
advanced records, playoff simulations, Branches and Sequels, False Assumptions
automation, improved search, deeper team history.

**The product succeeds or fails based on whether Matchup Lab turns the most
time-consuming weekly chore into mostly editing good drafts.**

### 41. Target End-State Experience

On a normal week the commissioner opens the Desk and immediately sees: the six
games, what is statistically unusual about each, the relevant history, the
manager-specific callbacks, three genuinely different premises per preview, a
full draft in the historical voice, and the evidence supporting every factual
claim. "Be the commissioner, editor, and comedian — not the research department."
