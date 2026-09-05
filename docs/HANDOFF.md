# HANDOFF

Updated 2026-09-05, end of the first Commissioner Portal tranche. Companions:
docs/SPEC.md (product spec), docs/DECISIONS.md, docs/DEPLOY.md (deploy
playbook), **docs/ROADMAP.md (ranked future work)**, POST_MVP.md (backlog).

This file is IMPLEMENTATION STATE. Future features belong in ROADMAP.md.

## Commissioner Portal, tranche 1 (2026-09-05)

**Status: 1,284 tests green (2 skipped; 62 new), build clean, both privacy audits clean, responsive at
320-1920. Nothing published, nothing deployed, no snapshot or Commissioner
prose touched.** Target architecture and the manual gates:
`docs/COMMISSIONER_PORTAL_ARCHITECTURE.md`. Ranked tranches: ROADMAP.

Measured before the work: a weekly cycle touched 13 distinct Desk pages and
asked for 14 approval clicks on a Disco week (8 sections + 6 previews), with
the editor at 175KB in one column.

What shipped:

- **Canonical preview.** `site_build.preview_snapshot()` builds an
  unpublished issue in snapshot shape; the Desk renders it through
  `public/issue_page.html` (`desk/canonical_preview.html` fills the new
  `private_toolbar` block). `templates/desk/full_preview.html` is deleted and
  `tests/test_preview_parity.py` stops it coming back. Assets come from
  `/commissioner/preview-assets/`, whose CSS is byte-identical to
  `dist/assets/<league>.css`.
- **Issue Room** at `/commissioner/{league}/{season}/issue/{key}/room`:
  sticky header, section rail with state tokens, one section in the centre,
  Preview/Research/QA beside it, publish drawer posting to the existing
  `publish-start`. Shares `desk/_section_card.html`, `static/desk-editor.js`
  and `_add_publication_state` with the long-form editor, which stays live.
  The Desk's next-action links now open the room.
- **CTP single approval.** `issue_builder.ctp_signature()` /
  `ctp_approved()`, `issue_modules.approved_sha`. Previews are written or
  not; the section is approved once and goes stale automatically.
- **WritingPacket** (`leaguepage/writing_packet.py`): one structured brief
  per section, four delivery modes, path redaction. Defined and tested, not
  yet wired into the Desk buttons — that is the next tranche.

Known and deliberate: Surfeit Week 1's composed CTP now differs from its
published snapshot by exactly the two inline provenance lines added in the
previous tranche. That is a correction to ship when he chooses, not a
regression here; Disco Week 1 composes byte-identical.

## Provenance tranche (2026-09-05)

**Status: 1,229 tests green (2 skipped), build and both privacy audits clean, responsive QC at
320/375/430/768/1024/1440/1920 with no horizontal overflow. Nothing pushed,
nothing published; no Commissioner prose or snapshot touched.**

The canonical model (also in DECISIONS):

    AI-generated                              origin ai, exact baseline
    AI-generated · Commish edited             origin ai, hash differs
    Automatically generated                   origin deterministic, exact
    Automatically generated · Commish edited  origin deterministic, edited
    Commish-written                           origin commissioner
    Commish-written · AI-assisted             origin commissioner, AI help recorded

Origin is structural. Editing does not erase origin. Exact reset restores
the exact state. The edit percentage is descriptive, Desk-only, never an
authorship inference. Matchup previews are Commissioner-written by product
rule. AI research/writing help is tracked on its own axis.

Where it lives: `leaguepage/provenance.py` (classify, record,
mark_commissioner, note_assistance, changed_from_baseline, desk_line,
inline_html, section_state, public_shape), four new columns on
`prose_provenance` (origin, assistance, baseline_text, event; the baseline
is private and never enters a snapshot), `templates/public/_provenance.html`
(replaces `_ai_provenance.html`; classes `.prov`, `.prov-mark`,
`.prov-label`, `.prov-detail`), and the Desk (`desk_editor.py`): origin is
settled on save (ROUGH DRAFT marker present before the first edit = AI
origin; empty section = his), proposal accept = AI origin, discard = AI
assistance, reset to a marked rough draft = AI origin, "Replace with my
copy" (`/edit/replace-origin`) = the one deliberate origin change, ranking
notes = Commissioner-written Peer and Near-Peer. The CTP parent badge and
`refresh_ctp` are gone: each preview carries its own line inline under its
heading, the optional intro its own. The matchup authoring contract in
`matchup_packet.py` now sends Claude's draft to `proposals/matchup--<slug>.md`.

Standing tabs: Force Flow, the Model Board and the team briefings say
"Automatically generated" under their headings. Retroactive: the backfill
(`scripts/backfill_provenance.py --apply`, run once) recorded AI origin for
the eight sections whose earliest saved revision carries the marker
(Surfeit draft custom/draft-capsules/hardware, four Surfeit week-01 matchup
keys of which two are current, Disco draft custom). Every other section,
Disco Week 1 included, has no known author and no label; published
snapshots carry no provenance and stay unlabelled and untouched.

Deliberately unlabelled: text pasted from outside the system, research done
in another window, an unresolved proposal nobody acted on. What the system
cannot see it does not claim.

## Publish pipeline fix (2026-09-05 morning)

The Disco Week 1 publish of 2026-09-04 evening deployed (dpl_Fe6g8iSMeUPShzuuEtkAea9QqcJc,
live and answering 200 on /, /disco/ and /disco/2026/week-01/) but its verify
stage failed one second later without logging why. The morning republish was
refused at the snapshot stage because the Fades and one matchup draft had
changed since; the Desk had no correction path. Now: failures are logged,
verification retries (6 x 5s) and records HTTP codes, the deploy-state record
distinguishes deployed / deployed-unverified / deploy-failed / never-deployed
and keeps the prior record when a job dies before the deploy stage, and the
publish page shows "Text since publication: changed" with a required
correction note that freezes `week-01.r2.json` beside the untouched original.
The wrong "deploy failed 10:35" record in the local DB was corrected by hand
to the verified truth. The first correction (r2, "League title fixed") then failed the build's
privacy audit on "the commish" in his own prose: the author's own aliases are
now a byline (exempt), QA and the build audit share one case-insensitive
matcher, and the real build passes with r2. **Nothing was published or deployed by Claude; the
correction note and the click are Jonathan's.** `published/disco/2026/week-01.json`
is still untracked in git and is the record of what shipped; commit it.

Later the same morning: the publish page now says what is live rather than
how the last job ended. Deploy records carry the revision they shipped, a
deploy marks every issue whose latest revision it carried (one Vercel deploy
is the whole site), unchanged text never fails (a leftover note is ignored
with a line saying so), and "DISCO CHAT updated 9 hours ago" sits at the top
of the publish page and on the Desk home cards (`publish_jobs.last_public_change`,
`Storage.list_meta`). The Force Flow morning loop is explored in ROADMAP,
not built. As of this note production carries Disco week-01 rev 1 and the
old masthead; rev 2 and the masthead ship with the next Disco deploy.

## Overnight editorial pass (2026-09-04)

**Status: 1,175 tests green (2 skipped; 43 new), public build 102 pages and privacy-clean, repo audit
clean. Nothing pushed, nothing deployed, nothing published.** Disco Week 1
stays published and its snapshot is byte-identical (sha256 485bee4e…, mtime
2026-09-04 21:39); Surfeit Week 1 stays `generated`. No Commissioner prose
was touched; the only new file in an issue directory that is his to act on
is `editorial/2026/disco/week-01/CORRECTION_CANDIDATE.md`.

Both Week 1 issues were read as a subscriber first. The defects were between
sections, not inside them, so the work went into checks and a brief rather
than new sections:

- **Editorial Command Brief** (`leaguepage/command_brief.py`): one private,
  deterministic page per issue (TOP STORIES, MATCHUPS TO WATCH, MARKET /
  ROSTER MOVEMENT, SOURCE DISAGREEMENT, CONTINUITY, EDITORIAL COLLISIONS,
  DATA WATCH, plus a statuses-only SCORECARD). Written to
  `COMMAND_BRIEF.md` in the issue directory (gitignored) by every research
  refresh and by its Desk page,
  `/commissioner/{league}/{season}/issue/{key}/brief`, linked from the
  workspace. Its top stories open `lowdown/PREP.md`.
- **Cross-section coherence** (`leaguepage/coherence.py`, wired into pubqa
  as the `coherence` category, warnings only): wrong-format advice (1QB copy
  in a Superflex paper and the reverse), raw Sleeper team names in copy,
  player attributed to the wrong roster (structured: callsign or team name
  after the player, inside the same sentence), out-designated players
  written up as available, one player carrying three or more sections, and
  paragraphs shared word-for-word with the other league's issue (research
  lanes only; the Lowdown and custom sections are his, written once for
  both papers).
- **Matchup writer brief** (`matchup_research.py`, `ghost_briefs.py`): byes
  and out-type players first in availability; weakest slot and same-position
  lineup calls from reference rank (never a projection); how each side was
  built (goes quiet from week 4); open takes and receipts touching either
  side.
- **NFL schedule** (`leaguepage/nfl_schedule.py`, `refdata/nfl/
  schedule_2026.json`): byes per week from the nflverse 2026 schedule,
  exported once from Fantasy Bot's cache. Absent schedule reads as unknown.
  No runtime dependency on Fantasy Bot.

Week 1 before and after, per league (pubqa on the assembled issue):

- Disco Week 1 (published): the HEAD checks reported nothing. The new
  checks report 5 warnings, all real: Hardware calls a team "Stafford&Son"
  (and "George & Friends", a name no roster carries, which only a team-name
  history could catch structurally); the CTP preview writes Josh Jacobs
  (Sleeper status NA, benched) as leading a room; two Fades sentences give
  1QB advice; Amon-Ra St. Brown carries three sections. All in the
  correction candidate. Nothing applied.
- Surfeit Week 1 (unpublished): 4 warnings: two Tracks sentences give
  Superflex advice in a 1QB league; Amon-Ra St. Brown and Bucky Irving each
  carry three sections. The pre-existing two proposals are still pending.
  No new proposals were written; the brief and the checks are the
  proposals.

Both briefs on real data: construction stories are capped at two and skill
positions only, Questionable tags are limited to top-two rooms and four
lines, special-teams rooms never make a story or a mismatch, and moves the
Commissioner selected on the Story Board appear in MARKET whether or not a
flag fired.

Deferred (ROADMAP, Tier 1 follow-ups): a second reference source for
disagreement; team-name history on sync; per-league angles to stop
Tracks/Fades reusing national-source paragraphs across both papers.

## Product evolution run (2026-09-03)

**Status: real build clean, repo audit clean, 100 pages, 872 tests green,
deployed and verified in production.**

Ten read-only recon agents audited the Commissioner workflow, the reader
product, editing, entertainment, fantasy analytics, the archive corpus,
mobile, accessibility, security and product strategy. Everything below was
verified against the code or the real database before it was acted on.

### BLOCKED — REMOTE AUTHORING

One item on the path was fixable without a secret and is now fixed: **CSRF
had no UI surface**, so `LEAGUEPAGE_AUTH_MODE=required` returned 403 on every
button in the Desk. The token now ships in a `<meta>` tag and
`static/desk.js` attaches it to every form submit and every same-origin
mutating fetch, centrally, so a new form or fetch cannot forget.
`tests/test_csrf_wiring.py` pins both halves. `/openapi.json` was also still
publishing the full private route map with the docs viewers off; it is off.

What still blocks remote authoring, in order, none of it doable from here:

1. **BLOCKED — REMOTE AUTHORING: seed `app_commissioners` in Supabase.**
   Run `.venv/Scripts/python.exe scripts/make_commissioner_seed.py` and apply
   the emitted SQL in the Supabase SQL Editor (or set `DATABASE_URL` in
   `.env` first and it applies itself). Verified 2026-08-31 as a hard
   boundary: `select` on that table with the publishable key returns 401, so
   no automation on this machine can do it.
2. **BLOCKED — REMOTE AUTHORING: create the private Vercel project** for the
   Desk and set its environment variables.
3. Code work that needs no secret and did not fit this tranche: prose
   repository cutover (a sections table replacing `editorial/**/*.md`),
   durable jobs (a `jobs` table replacing the `_JOB`/`_JOBS` process
   globals), and the 47 filesystem write sites a read-only serverless
   runtime rejects.

### Numbers the site published that its own models contradicted

- **The valuation stage switch was published as roster movement.** Player
  values change method at three played weeks; nothing compared stages, so
  seven of ten teams "moved" three or more places in a room on rosters
  nobody touched. Positional deltas now require both snapshots to have been
  measured the same way.
- **Playoff odds were printed as 0% and 100%** from a 2000-draw simulation,
  and rounded to a tenth of a point on a figure whose Monte Carlo error alone
  is a couple of points. `format_odds` says `<1%` and `>99%`.
- **The odds delta printed a percentage on the same page that refuses to show
  one** in the bands stage, because the snapshot never recorded the outlook's
  stage. It does now.
- **"The result moves a playoff berth"** fired on standings position and
  rendered beside a leverage model reporting a zero swing for two teams
  already in. Split into `Playoff Leverage` (a cutline) and `Seeding at
  Stake`.
- **One room was printed as both a team's strength and its concern.** `min`
  and `max` both return the first extreme element, so a team whose skill
  rooms rank alike got the same room twice. The board now says no room
  separates the roster, which is the honest version of the same fact.
- **"REACH, 244 picks early" in a 228-pick draft.** A reference rank past the
  end of the draft is `off_board` and carries no magnitude. The league's
  boldest picks and the consensus-style label are skill positions only, which
  flips one real team's label.
- **`weeks_played` is a count, not a week number.** One unsynced week put an
  already-played week inside "remaining" and re-simulated it against random
  opponents.
- "a loss ends it" for a team alive by arithmetic; "matters" for a swing of
  exactly zero; a rank measured after the fact described as "before the
  move"; a kicker add with no drop called routine churn by checking the adds
  against themselves; `recent_form` labelling every team with the league's
  window including one that played fewer weeks of it.
- Benchwarmer Memorial and Galaxy Brain nominated kickers, twenty lines from
  the bench-swap code that refuses to. Four awards were "strong" whenever
  they had any nominee at all.
- The Change Inbox read FAAB from `waiver_budget` only, so a 45%-of-budget
  claim was filtered out as a routine add/drop.

### Privacy

- **The publish gate and the build audit disagreed, with an irreversible step
  between them.** `pubqa` scanned handles and display names; the build audit
  scanned aliases too, and runs after the snapshot is frozen. A private first
  name in issue prose passed QA, became immutable, and only then failed the
  build. Both now scan the same set.
- Matching is case-insensitive and boundary-anchored, and reads page prose
  rather than stylesheets. That immediately caught **a real 2019 archive
  quote naming a manager by an alias, live on the Disco matchups page**;
  `history.py` now drops such a candidate and takes the next one instead of
  poisoning the build two stages later.
- The published-name subtraction tested containment against every team name
  joined together, so an alias sitting inside some *other* team's name was
  dropped from the scan everywhere. Measured at eleven candidates on live
  data, six of them first-name shaped.
- **`leaguepage/privacy.py` is one list for both audits.** They disagreed: a
  Supabase project URL or a Postgres connection string was blocked from
  `dist/` but committable to
  `main`, and an AWS key was the other way round. The repo audit now reads
  aliases and display names, subtracts published nicknames from the local DB,
  and covers `data/`, `backups/`, `dist/`, `PREP.md`,
  `commissioner_notes.md`, `.pem`, `.key` and the rest it was missing.
- The build copies only assets a page references, in shapes a public site can
  serve. That drops 176KB nothing asked for and closes the hole where a
  `.csv`, `.pdf` or `.db` dropped into `static/` shipped unexamined, since
  the audit skips file types it cannot read.

### New public surfaces

- **Seasons Past** — six seasons of Disco champions and last place, 2019 to
  2024, read out of the mastheads that recorded them. Sleeper reaches back
  one season; 2024 has no archived issue at all and survives only because the
  2025 mastheads carried it forward. Names link to team pages through
  CONFIRMED aliases only. Big Daddy AF stays out of both leagues, and The
  Surfeit correctly has no ledger.
- **Week N in numbers** — the five superlatives from the last completed week,
  with evidence. `weekly_awards.py` is 387 lines that reached no reader,
  because publishing an award means picking a winner and that is the
  Commissioner's job. These are facts, not verdicts; a test asserts no row
  ever declares a winner of anything, and the private nomination slate is
  untouched.
- **Reality Check** — the gap between a team's record and the record its
  scoring earned, in games, with the denominator named. Careful about the
  arithmetic trap: an unbeaten team cannot sit below its own all-play, so
  those rows get their own sentence rather than being called lucky.
- **My Team** — a reader picks a team once, in their own browser.
  localStorage only, no account, no network, clearable from the page that set
  it.

### Editing and reading

- 22 team pages each reprinted 95 words of methodology. The explanation now
  lives once on the page that owns the measurement, and the team page links
  to it: roughly 1,200 words removed with the provenance intact.
- The archive listed "2023 Disco Week 1" under a 2023 heading that was really
  2022, on 14 of 42 Disco issues. The masthead volume line agrees with the
  frontmatter, not the title, and the archive is source data nobody should be
  silently rewriting, so listings are labelled by the indexed season and week
  with the filed title beside them when it disagrees.
- Eleven matchup cards carried a "Scout view, computed, not the Commissioner"
  heading for one fact the page already states once above them.
- A team page told a reader who they play next and gave them no way to go and
  look at them. The opponent's name is now the link.
- The Commissioner's own capsule prose reaches the Peer and Near-Peer page.
  It was parsed, discarded, and the page rendered `None` into a slot that had
  been waiting for it, so a page whose entire subject is his judgment carried
  zero words he wrote.

### Mobile and accessibility

- `tabindex` appeared **zero times in the whole build**, so the columns past
  the fold of a 229-row draft board were unreachable without a pointer. Every
  scroll container is now focusable, labelled, and a real region.
- A rule written for editorial prose tables was also catching the site's own,
  making them `display:block` and voiding `scope` and `<caption>` on twenty
  of twenty-four while the wrapper meant to scroll sat inert.
- The team brief skipped h2 to h4 on 21 pages. Around 130 links stood between
  15 and 20 pixels tall. The team-strip border was 1.30:1 on a background
  1.09:1 from the page. Sorting reordered up to 229 rows and announced
  nothing.
- **The My Team shortcut rendered for everyone**, because every `display`
  rule on the page outranks the browser's own `[hidden]`.
- The Desk had no focus styles at all, no responsive rule on seventeen of
  twenty-one templates, a writing textarea at 14.7px that makes iOS Safari
  zoom on every focus and never zoom back, character-sized inputs pushing the
  ranking screen to 2.20x the width of a phone, and `<a><button>` nesting on
  Preview and Publish. The ranking screen and the editor are both 1.0x now.

### Commissioner's Desk

**Mission Control.** The Desk home answered nothing: a SYNC button and two
status words per league. It now computes the answer — how stale the data is,
how much is undecided and how much of that is above the noise floor, sections
approved out of included, what would refuse to publish, and one next action
naming the earliest step that is actually blocked. Suggesting "publish" while
four sections are empty is a button, not guidance. It reads and never
decides; a test enforces that.

### A class of bug nothing was looking for

A patch script written as a shell heredoc silently turned a regex word-boundary
escape into a literal backspace (0x08), three times in one session. The pattern
compiled, ran, matched nothing, and produced pages that looked plausible.
`tests/test_source_hygiene.py` now scans the tree for control characters — and
immediately found a **fourth**, in `pubqa.py`, from an earlier tranche: the
placeholder gate could not match a bare XXX marker and had not been able to for
some time. A regex that quietly matches nothing is the worst failure mode
available to a codebase whose entire discipline is refusing to publish claims
the data does not support.

### Closing the tranche (2026-09-04)

- **One stylesheet instead of ninety-eight copies of it.** The CSS was
  inlined into every document: 728KB of the build's 1.9MB of HTML, 74% of
  the bytes on the smallest pages, re-sent on every click through a
  fifty-five issue archive. It is one cached file per theme now
  (`templates/public/_site_css.html`, emitted as `assets/{slug}.css`).
  Total HTML fell from 1,916KB to 1,276KB; the Surfeit archive index went
  from 9,939 bytes to 2,951. The failure mode is silent and total, so
  `tests/test_stylesheet.py` checks the link resolves from every depth and
  a sweep of the real build confirmed all 97 league pages resolve theirs.
  The root league-select page keeps its own 25 lines: a third stylesheet
  for one page nothing else shares costs a request to save a kilobyte.

- **The prose-table rule was scoped to `.prose`.** It had been
  `section.module > div table`, which caught every table the site builds
  itself. Two invisible consequences: `display:block` stops a table being a
  table for assistive technology, so `scope="col"` and `<caption>` quietly
  stopped counting; and the table took the scrolling from the `.tablewrap`
  wrapper, which sat inert while carrying the focusable region. Verified in
  a browser: all 13 wrappers on the draft page scroll, all 13 tables are
  real tables, and the two editorial prose tables still get the treatment
  the rule was written for.

- **Static assets are copied only if a built page references them**, and
  only in shapes a public site serves. That kept `static/desk.js` out of the
  public build — it had been copied to `dist/assets/` on every deploy — and
  a 176KB logo nothing points at. The build warns about each skip rather
  than dropping it silently.

- **The My Team nav shortcut worked on one page.** It validated the stored
  slug by looking for a card, and the cards only exist on the home page, so
  it resolved there and was hidden on the other 96. Every page now ships its
  own league's slug list on the shortcut. Verified in production at three
  depths, in both leagues, with a stale slug and a live one.

- **Off-board picks carry no magnitude.** `off_board` was set in
  `enrich_picks` but `summarize_team`'s field whitelist dropped it and the
  `_dv.html` macro had no branch for it, so the label still quoted the
  number. Live now: Disco's biggest printed reach fell from 244 picks to
  141 in a 228-pick draft, Surfeit's from 131 to 48 in a 150-pick draft, and
  Surfeit's five "boldest picks" went from four kickers and defenses to five
  genuine skill-position reaches.

- **Tracks and Fades needed a real divergence.** `win_pct - ap_pct >= 0.2`
  is a property of the extremes: an undefeated team has a win percentage of
  1.000, so any all-play under .800 cleared it and a 70% all-play — elite —
  was nominated as a Fade for being elite. The same arithmetic made every
  winless team a Track. The all-play now has to sit on the wrong side of
  average, and the two numbers have to cover the same games; `record` is
  season-to-date while all-play runs through the previous week.

- **A rank with no score behind it.** `max(0, 250 - rank)` clips every
  player past the end of the reference board to exactly 0.0 — the same as no
  player at all. A room of those ties every other zero room and the stable
  sort orders them by roster_id. `positional_profile` now reports which
  rooms it actually measured (`rated`, read through `is_rated`), and the
  briefing, the scout view and the model board decline to name a room they
  cannot tell apart. Inert on real data; it fires where the board genuinely
  stops.

- **Lineup efficiency was inflated by ghosts.** A rostered player missing
  from the cached players dict has no position, so no slot takes him and he
  is left out of the optimal lineup. The `actual > optimal` guard caught it
  only when he was started; if he sat, a 25-point ghost turned a real 84%
  into a reported 100%. Both optimal-lineup docstrings also claimed an
  optimum that greedy flex filling does not guarantee — safe for both live
  leagues' slot orders, but that is a property of what Sleeper returns.

- **Test fixtures gained positions and a reference board.** `populate_league`
  left rosters empty and no synthetic player had a position, so most of the
  analytics ran their "nothing to say" branch under test while production ran
  the other one. `season.stock_rosters` and `season.synthetic_adp` fix that;
  `save_players` never overwrites a player a test set up itself.

- **The Desk rankings screen compared week 5 against August.** "Prev" was
  hardcoded to the preseason board. It is the previous board now, a new week
  seeds its ranks and tiers from it (marked unsaved until submitted, notes
  never carried forward), and "Approve all ready" calls the same per-section
  approve once per section rather than adding a second path to approval.

### 2021, reconstructed (2026-09-04)

`leaguepage/archive_results.py`. The archive states almost no results as
results — every Matchup Roundup block is a PREVIEW, written before kickoff,
and there are exactly two sentences of head-to-head prose in fifty-five
issues. But a preview prints each team's record and so does the next
week's, so the week between them is recoverable from the difference. That
gives **52 Disco games across weeks 1 to 13 of 2021**, a season Sleeper
cannot reach: the API goes back one prior season.

All 52 were re-derived by a separately written parser reading the raw
markdown. 52 agree, 0 disagree.

The design bet is that the failure mode is a **miss, never a wrong result**.
A game appears only when exactly one side gained a win and the other gained
a loss, and a week whose own headers contradict themselves is not used at
all — including the weeks either side, since they resolve through it. Each
known corpus defect is caught by a check that does not depend on noticing
that particular defect:

| defect (all live in the corpus) | what catches it |
|---|---|
| week 8 puts EMCO in two matchups | no team may appear twice in a week |
| week 13 types a record `(63-9)` | every record in week N covers N-1 games |
| week 8 writes odds `52-48` not `52/48` | records only come from inside parens |
| `PITCH` vs `Pitch` | case-folded canonical names |
| a proxy drafter appears as a team | week 1's `The Dude/Glory` header teaches the alias; the parser learns it from the corpus rather than being told who somebody is |

Weeks 7 and 8 are therefore absent, and the page names them and says why.
Standings rank on percentage because a dropped week leaves teams on
different game counts. **Winners only** — the previews carry records, never
scores, so the margin is not recoverable and is not invented.

`title_tension` compares the two independent readings of the same archive:
McLovin led the recovered weeks at 9-2 and the masthead's Seasons Past
ledger records Babe as the 2021 champion, so the page says that rather than
leaving a reader to notice two tables disagreeing.

Scoped like every other archive surface via `ARCHIVE_SCOPE`: Surfeit has no
corpus and its page carries nothing rather than borrowing Disco's. A season
is dropped **whole** if any one of its names is not already published inside
a current team name, because publishing half a season misstates every record
in it — verified to have teeth by forcing a name private and watching the
season vanish. Team names link to current team pages only through CONFIRMED
aliases; a name that does not resolve still prints, it just is not a link.

`MIN_WEEKS = 6` keeps a thin season out: three scattered weeks is a
curiosity, not a record. 2019, 2020, 2022, 2023 and 2025 do not reach it.

## Overnight product run (2026-09-03)

**Status: 10 commits, 675 tests, real build clean, deployed and verified
byte-identical to `dist/` across all 98 pages.**

Six read-only recon agents audited the workflow, the analytics, the public
product, the UX, data integrity and the privacy boundary. Everything below
was verified in the code or the real database before it was acted on.

### Numbers that were wrong

- **The playoff model read `records[rid]["points_for"]`; `team_record`
  returns `fpts`.** Every simulated season started every team at zero
  points, so the tiebreak deciding the last playoff spot discarded all the
  scoring that had happened. The same bug made week-0 snapshot standings
  degenerate to roster_id order, so preseason now stores no standings
  baseline and week 1 cannot announce movement nobody made.
- **FAAB was read from `waiver_budget` at four of five call sites.** That
  list carries budget moving between teams in a trade and is empty on a
  claim, so every waiver claim looked free. `matchup_analysis.faab_cost` is
  now the single reader.
- `recent_form` collapsed its window to one pseudo-week before computing
  all-play, cross-producting weeks and inflating the game count.
- `late_season_leverage` had a weight and a tag and no component that
  emitted it, so `Playoff Leverage` could never fire.
- Three K/DST leaks: roster contrast lines, the bench-swap award, and
  `team_outlook`, which was publishing "K room ranks 2/10" as *defining
  this team right now* in production.
- A kicker premium could headline the front page as a live receipt:
  `candidate_takes` guarded that at capture time, the archive-derived path
  in `receipts.py` did not.
- **A tie was a loss for both teams** (`pts > opp` is False at 100-100), and
  the take engine recommended BUSTED on a game nobody lost.
- An empty roster ranked near the top of every room and published "with
  real depth behind the starters".
- A week Sleeper returned unpaired counted in the standings while the CTP
  rendered it empty.

### The frozen record

`publish_assembled_issue` overwrote `published/<key>.json` in place with no
revision and no "Updated" line, and the ordinary route to that was a deploy
that failed after the snapshot stage and got retried. An identical re-entry
is now a no-op; a changed one is refused and pointed at `revise_issue`.

Receipts are scoped to their own season, so the first sync of 2027 will not
resolve every 2026 claim at once.

### The real schedule, and what it unlocked

Sleeper publishes the whole regular season's pairings up front and fills the
points in as they are scored, so a future week returns real `matchup_id`s
and zero points. `ingest` fetches them once (idempotent: a future week with
pairings already stored is skipped), `team_analytics.remaining_schedule`
reads them, and the playoff model simulates the schedule that will actually
be played.

That made `leaguepage/leverage.py` possible. Both numbers come out of one
simulation run by conditioning inside it:

    leverage(team)   = P(playoffs | they win) - P(playoffs | they lose)
    rooting(team, g) = P(playoffs | A wins g) - P(playoffs | B wins g)

Nothing shows below a five-point swing, and a five-point swing off a
two-percent base is a formality rather than a stake, except elimination.
Matchup cards carry what the game is worth to each side; team pages carry
their own stake plus the other results to root for.

### The public product

- **Sharing.** All 98 pages had exactly two meta tags. They now carry a
  page-specific description, canonical, OpenGraph and Twitter tags.
  `config.SITE_URL` (env `LEAGUEPAGE_SITE_URL`) is the base.
- **The link graph.** 82 of 98 pages were dead or near-dead ends. Team names
  are links everywhere via `templates/public/_links.html`; issue, team and
  archive pages carry a footer of exits; the 55 archive issues link to their
  chronological neighbours. Zero dead ends now, one near-dead (the Surfeit
  archive, which holds one issue).
- **Peer and Near-Peer** reads the ranking he published as prose
  (`leaguepage/published_ranking.py`) and prints the three widest
  disagreements with the model. The parser refuses anything that is not a
  complete, unique, one-based ranking covering three quarters of the league.
  A `power_rankings` row still wins when one exists.
- **Preseason honesty.** Standings and the teams grid published `#11` off a
  roster_id tiebreak on twelve 0-0 teams. Preseason now says there is no
  order to publish and points at the model board.
- **Lineup efficiency** shipped: cumulative actual over optimal, plus points
  left on the bench. When actual exceeds the best legal lineup the
  reconstruction is wrong (a player missing from the cached players dict),
  so it reports nothing rather than 214%.
- **Ledgers.** Transactions carry *how it aged* (the dropped player followed
  wherever he went, and whether the room moved); the draft carries *what
  happened to the picks*. Neither re-scores the call made at the time.
- **Black Box** carries closest-to-the-mark, margins, per-row anchors and a
  door into the archive.

### Mobile, accessibility, print

Verified at 375px against the built site: every page has a document
scrollWidth of exactly 375. Editorial tables (raw HTML from published prose)
were the only whole-page horizontal scroll and now scroll inside themselves.
`role="button"` on a `th` was overriding its columnheader role on 194
headers; the control is now a real button inside the cell. Section headings
are larger than body text; an injected editorial heading no longer outsizes
its own section. The root index had two h1s, no `main` and no skip link.
There is a print stylesheet.

### The build audit

`audit_output` knew 17 literal strings and looked only at `.html`: fourteen
of sixteen classes of private data would have shipped. It now matches eleven
private *shapes*, scans every text file in the build, and names the class
and offset rather than quoting the value. Aliases are scanned too, minus any
that appear inside a public team name -- managers put their own nicknames in
their team names, and scanning aliases without that subtraction flagged 103
violations on a clean build. `build_site` returns `public_names` so the
deploy script can pass them in.

### Commissioner workflow

- **Inbox triage now reaches the issue.** "Add to Issue" on a `change:*` id
  reached nothing: the builders iterate the story candidate list and a diff
  item was never in it. `change_inbox.as_candidates` merges them in, and a
  story routed to the Lowdown produces a brief instead of mapping to `None`.
- **The ranker has a working scale.** Every non-matchup candidate arrived at
  a flat 0.4 and scored exactly 16, so half the inbox sorted alphabetically.
  A nine-seed upset scored 38 (Minor) against a free bench-piece trade at 40.
  The repetition lane is now kind *plus subject*, so one standings story no
  longer penalises all twelve teams.
- Save for Later re-surfaces while the item is still true; every item says
  why it matters, not just why it scored; triage takes a note and can be
  reopened.
- The Lowdown brief listed draft reaches under STRONGEST NUMBERS in week 9;
  it now leads with the season once games have been played. The stale-prose
  chip fired on every section every sync; it now compares the brief's own
  content.

### The publication gate

Five blockers with no override fired on ordinary prose: `man-vs-machine`,
a code span containing `**`, "coming soon" mid-sentence, Roman numerals
(`XXX`), and a markdown footnote. All fixed, with the real defect still
caught in each case. Two misses closed: a lowercase `roster 4` and a
relative image `src`.

### Known and deliberately not done

- **Git history still contains one manager key** in commit b2ffa1b (a
  slugified team name that appears in every public URL anyway). HEAD is
  clean and the repo is private; history was not rewritten.
- Odd team counts / BYE weeks: a bye team gets no matchup card. Both real
  leagues have even rosters, so this is hypothetical.
- The Commissioner-vs-model comparison reads the published prose. If he
  starts entering rankings on the Desk, that path takes over automatically.

## Takes and Receipts shipped (2026-09-03)

**Status: built, 532 tests, exercised end to end against a copy of the live
database. Zero real takes exist — nothing is public, which is correct.**

The receipts engine's limit in the previous tranche was that claims had to be
guessed out of prose. Now the Commissioner marks them.

- `leaguepage/takes.py` — inference, lifecycle, six deterministic evidence
  hooks, retroactive candidate scan, public rendering.
- Schema **reuses the existing `takes` table**; new columns via the
  established `_migrate()` path plus `migrations/0003_takes_lifecycle.sql`
  so the Postgres cutover surface does not drift. `subject_roster_id` is the
  stable subject link — team slugs derive from the public name and move on a
  rename.
- **Two columns, two boundaries.** `status` is his verdict,
  `recommended_status` is the engine's; a disagreement stays visible. `public`
  defaults to 0, so an unreviewed take cannot leak.
- **Lifecycle:** open / too_early / leaning_right / leaning_wrong /
  resolved_right / resolved_wrong / void. The two leaning rungs exist so a
  take that loses one week is not called wrong. Legacy vocabulary
  (validated/contradicted/retired) maps to the canonical set.
- **Evaluation gates before hooks:** the take's own review horizon, then a
  per-topic sample floor (`MIN_WEEKS`: playoff 6, power 4, roster/draft/trade
  3, matchup 1). Both answer TOO EARLY.
- **Draft claims never re-classify REACH/STEAL** — that stays immutable
  market analysis. They read roster status, starts and points. Special-teams
  players are reported but never carry a verdict: kickers start every week,
  so "did he start" says nothing.
- **Editor:** select a sentence, press *Track this take*, an inline panel
  opens. `verbatim` is decided by comparing the quote with the section source,
  not a checkbox; a paraphrase renders as "wrote, in substance" and never
  inside quotation marks. Subject falls back to heading context.
- **Receipts reach the Change Inbox as a story type**, not a competing queue,
  and only once the engine has leaned somewhere.
- **Public path has three gates**: he marked it public, the engine moved it,
  and provenance survives. `pubqa.check_receipts` runs in the build and
  blocks missing provenance, a paraphrase dressed as a quote, private fields
  travelling with a receipt, a handle inside a quote, and roster placeholders.
- Evaluation runs during Sync (`takes_eval:{league}` timing) and the public
  build only reads the stored result.

Retroactive candidates on the real 2026 issues: **3 Disco, 8 Surfeit.** The
scan drops signposting, pleasantries, league-wide summaries misattributed to
the last capsule, and any claim only about kicker/defense "premiums" — the
calibration decision enforced at capture time.

## Tier 2 shipped: the consumer half (2026-09-02)

**Status: built, tested (469 tests), privacy audit clean, deployed.** The
product question moved from "can it calculate useful things" to "does a
league member understand what matters, enjoy reading it, and click again".

### 1. Publication quality gate — `leaguepage/pubqa.py`

Six categories plus privacy: identity, placeholder, formatting, copy,
freshness, analytical consistency. **Blockers stop publication; warnings
never do** — a warning IS the override. **Privacy blockers have no override
path anywhere**, deliberately.

- The design constraint that shaped every detector: *voice is not a defect*.
  Fragments, slang, deliberate capitalization and Air Force jargon are the
  product. Every copy check is mechanical and unambiguous. Thirteen samples
  of Jonathan's real published prose are pinned as must-not-flag tests. If a
  future change makes the gate nag about style, those tests fail.
- Team-heading identity resolution works on LEVEL then SHAPE: "Second
  Opinions" sitting at the same `###` level as twelve numbered team entries
  is not mistaken for a team. A heading fails only when it carries a token
  belonging to NO current public name, so "The Dude" for "The Dude Abides
  (The Dude)" passes and "Babe (confedfatties)" does not.
- The K/DST analytical check guards the 2026-08-30 calibration decision: a
  section that ranks teams and leans on two or more raw special-teams
  consensus deltas without the disclosure gets warned.
- Surfaces: Publication Check panel on the editor and publish pages (Accept /
  Edit / Ignore per suggestion), `publish_assembled_issue` and `revise_issue`
  both re-run the gate, `scripts/qa_issues.py` audits from the terminal.
- Ignores live in meta `qa_ignored:{league}:{season}:{issue}` and are honored
  for warnings only.

### 2. Corrections — additive, never destructive

`publish.revise_issue()` writes `<key>.r2.json` beside the original. The
original stays on disk and in git as the record of what shipped that day;
the site renders the newest revision and prints "Updated <date> · <note>".
`_load_snapshots` collapses a family to its newest revision.
`scripts/apply_qa_fixes.py` applies ONLY mechanical COPY findings (those
carrying an exact `fix_from`/`fix_to` pair) and updates the editorial source
so the typo cannot return. **Disco's 2026 Draft Issue is at revision 2**
(comma splice, doubled period, "way to many").

### 3. Front page — `leaguepage/front_page.py`

`season_state()` reads games PLAYED, never the calendar. Item builders
propose weighted stories; strongest leads, next few are secondary, capped at
five, floored at two — a thin week ships three rather than five padded ones.
Preseason drops the 0-0 standings table for skill-position room leaders; the
playoff-race state leads with the cutline. The author rule extends here: his
team cannot be Team to Watch.

### 4. Dead-end elimination — `leaguepage/model_views.py`

Scout View (matchups) and Model Board (Peer and Near-Peer) are labelled
scaffolding that never imitates the Commissioner — no jokes, no verdicts, no
predicted winners. Scout View returns None when a matchup has nothing to say.
Model Board weights results at `min(0.7, 0.12 * weeks_played)` and stays on
as a comparison column once his ranking publishes.
`test_no_primary_route_is_a_dead_end` walks every nav tab in both leagues.

### 5. Team briefings — `leaguepage/team_briefing.py`

"Your Team This Week" answers the five questions above the tables.
`editorial_strengths()` separates analytical rank from editorial importance:
the positional table still ranks K and DEF, but a special-teams room reaches
the headline only when it is league-best or league-worst.
`section_order()` ages the Draft Recap down the page by season stage without
ever dropping it. **Team Draft Recaps now use `headline_deviations`** — they
were headlining kickers (Fairbairn, Cam Little) as Biggest Reach.

### 6. Live history — `leaguepage/history.py`, `leaguepage/receipts.py`

Archive callbacks go back to the issue BODY and take the whole sentence
containing the match, not the FTS snippet (a fixed-width window that starts
and ends mid-word). `reads_as_prose()` rejects draft-result lists, injury
tables, matchup headers and anything a quarter numeric. Receipts quote
claims verbatim, test them against current rosters and room ranks, and
describe evidence rather than passing verdicts; position claims need three
played weeks. **Repetition: facts may repeat, callbacks may not** — a
recently-surfaced archive quote is dropped outright, while "Team 1 leads
2–1" is the current state of the rivalry and belongs on the page weekly.
Surfacings are recorded per week in meta (`history_shown:` /
`receipts_shown:`), which keeps rebuilding a week idempotent.

**Open, needing Jonathan's judgment (the gate found them; it will not
guess):** Surfeit's published Lowdown still says "Jesse (team name pending)"
— a hard blocker that prevents any future correction to that issue until the
line changes; its power rankings head roster 4 as "Jesse", which matches no
current public name; Disco's rankings head teams as "Tua Girls One Kupp
(Ethen)" and "Babe (confedfatties)"; and the Surfeit rankings order teams by
summed raw K/DST deltas without the calibration disclosure.

## Tier 1 shipped: the weekly triage loop (2026-09-01)

**Status: built, tested, exercised against a copy of the live database. Not
deployed anywhere public (it is Desk-only, and the Desk is localhost).**

Sync now records a snapshot, the Change Inbox diffs it, and one shared
significance model ranks everything with its reasoning attached.

- `leaguepage/significance.py` — the shared interpretable scorer. Named
  components each carrying points and evidence, clamped 0..100, same idiom as
  `matchup_interest`. Two NEGATIVE components (repetition, triviality) do the
  real work: without them a long tail of true-but-boring facts outranks a
  genuine upset by accumulating small positives.
- `leaguepage/change_inbox.py` — `capture_state` / `record` per sync, then
  `diff_snapshots` + `transaction_items` + `receipt_items` merged with the
  existing `weekly_story_candidates` and ranked. Every item carries BEFORE and
  AFTER, not just a headline.
- `sync_snapshots` table (migration `0002_change_inbox.sql`). One row per
  MATERIAL sync: an identical payload is not stored, so pressing Sync twice
  does not blank the inbox. `snapshot_id`, not `taken_at`, is the ordering key
  because two syncs in the same second are ordinary.
- **No new decision store.** Add to Issue / Ignore This Week / Save for Later
  are `story_decisions`' existing include/ignore/save plus `route`, so an inbox
  decision is the same row the issue builder and authoring briefs already read.
- `/commissioner/inbox` plus a WHAT CHANGED? button on Desk home. Mobile-first
  (thumb-sized actions, no horizontal tables, its own media query).
- **Postgame auto-refresh**: `matchup_packet.phase_of` flips a brief from
  preview to result once real points exist, and the result block forbids
  claiming causation the data does not support. `refresh_issue_research`
  already ran on every sync and already preserved prose; it now also gets
  timed. There is no separate recap workflow, by design.
- Sync and inbox timings are recorded on the sync job (`_timing`) and shown
  under the inbox, so "do not make Sync feel slow" is measured.

Real defects the acceptance run caught, all fixed:

1. Two syncs in the same second silently overwrote each other (timestamp PK).
2. Ordinals rendered "2th" and "3th" in Desk-facing copy.
3. Diffing week 1 against a PRESEASON baseline reported all twelve teams as
   standings movers, because preseason order is an arbitrary tiebreak among
   0-0 teams. Standings items now require games on both sides of the
   comparison. Item count on the real acceptance scenario: 25 -> 14 -> 10.
4. The existing Story Board was being flattened to a constant magnitude, which
   sank every matchup below every change item. Matchup candidates now carry
   their real Competitive Importance and Story Value into the ranking.
5. `/commissioner/inbox` raised NameError on a missing local import; the route
   tests now render every inbox surface.

Not yet done inside Tier 1: streak and all-play divergence live in the
snapshot payload but are not yet their own change items (they reach the inbox
through the existing analytics candidates). See ROADMAP.md.

## The All-City Team sidebar feature (2026-08-31)

**Status: built, validated, previewed. Nothing published.** The 2026 edition
is drafted into disco week-01 and still carries the ROUGH DRAFT marker, so it
is double-blocked from publishing (unapproved plus blocked marker) until
Jonathan edits it. The module is opt-in and is NOT included on any issue in
the real DB; include it on the Desk when you want it.

- `leaguepage/all_city.py` validates an edition and renders the table, the
  rule footnote and the near-miss list. `editorial/features/all-city/` holds
  the editions plus a README with the rule and the rerun procedure.
- New issue module `all-city` ("The All-City Team"), kind `all-city`, in
  `OPT_IN_MODULES`. Prose lives at `sections/all-city.md` like any other
  section, so the Desk editor needed zero changes.
- Editions bind to one `(season, issue_key)`; a rerun is a new file. There is
  deliberately no "latest wins" fallback.
- 2026 lineup: Josh Allen, Bijan Robinson, Jonathan Taylor, Ja'Marr Chase,
  Justin Jefferson, Colston Loveland, Brandon Aubrey. Five of the seven
  replaced the candidates in the original brief on consensus rank. Ja'Marr
  Chase's left knee (hyperextended 2026-08-25) is the one thing to re-check
  before this publishes.
- The rule is municipal class, never population (docs/DECISIONS.md). It costs
  the roster the consensus RB1, TE1 and TE2, which is the joke.
- **Second variant, same machinery:** module `all-city-marquee` ("The
  All-Marquee Team"), editions in `editorial/features/all-city-marquee/`, is
  the same exercise with `rules.minimum_population: 100000`. Both are drafted
  into disco week-01 and both are unpublished. 2026 marquee lineup: Josh Allen,
  Omarion Hampton, Bucky Irving, Drake London, Parker Washington, Tyler Warren,
  Tyler Loop. Two printed rulings there: the Washington, D.C. exception (the
  sources genuinely conflict) and reading the allied-cities clause as "no
  QUALIFYING U.S. city", which is what puts Drake London on Greater London.
- The 100k floor is expensive and that is the point: it costs the consensus
  RB1, RB2, WR1, TE3 and K1, and leaves two qualifying kickers in the whole
  league, both named Tyler.
- `tests/test_all_city.py` (55 tests) guards both shipped datasets, the
  exact-match rule, roster completeness, the population floor, the column
  allowlist, and the public/private field split.

## Remote authoring — Phase 2: Supabase auth (2026-08-31)

**Status: sign-in works end to end against the real Supabase project.**
Migration 0001 is applied; `scripts/verify_supabase_schema.py` reports
16/16 tables present and locked against the anon key. A real OTP was
requested and delivered, and the identity record now exists (confirmed by a
`should_create_user=False` probe returning 200).

`.env` exists at the repo root, gitignored (`git check-ignore` verified),
holding `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
`LEAGUEPAGE_COMMISSIONER_EMAILS`, `LEAGUEPAGE_AUTH_MODE=off` and a
generated `LEAGUEPAGE_SECRET_KEY`. Auth mode stays `off` locally so the
localhost Desk keeps working without ceremony; the hosted deployment sets
`required`.

Trap that cost a debugging cycle and is now covered by a test: `auth.py`
originally read `os.environ` directly, and nothing called
`settings.load_env()`. A `.env`-configured allowlist was silently ignored,
so the Desk looked configured while sitting in open fallback mode with an
empty allowlist. **All auth config must go through `settings.get()`.**

Design decisions worth not re-litigating:
- **Email OTP, not magic link, for Supabase.** OTP needs no Redirect URL
  registered in the dashboard, so sign-in works locally today and the
  hosted URL can be added later without code changes. Magic links can be
  layered on afterwards.
- **The OTP exchange is server-side.** The browser never receives any
  Supabase key, not even the browser-safe publishable one. Tested.
- **Two independent gates.** Supabase answers "who is this"; the
  `LEAGUEPAGE_COMMISSIONER_EMAILS` allowlist answers "may they use this".
  A valid Supabase user who is not listed is refused, and the address
  authorized is the one Supabase returned, never the posted form field.
- **One auth system.** Supabase plugs into the Phase 1 central route guard;
  there is no second session mechanism.
- **No email in URLs.** A signed short-lived HttpOnly cookie carries the
  pending sign-in, so addresses stay out of history and access logs and the
  allowed/rejected responses are byte-identical.

Schema: `migrations/0001_commissioner_state.sql`, applied. RLS is enabled
*and forced* on every table with a policy that consults `app_commissioners`
(not "any authenticated user"); `anon` is granted nothing on any table or
sequence.

**The one bootstrap step that cannot be automated:** `app_commissioners` is
empty, and because RLS is forced on that table and its own policy requires
membership, nobody can insert the first row. The anon key is granted
nothing, so the application cannot do it either — deliberately, since an
app that could write its own allowlist would not have an allowlist. Run
`.venv\Scripts\python.exe scripts\make_commissioner_seed.py`. With
`DATABASE_URL` set in `.env` it applies and verifies the seed directly;
without it, it writes the idempotent `insert` to
`backups/seed_commissioner.sql` (gitignored) and the clipboard when
available, for the Supabase SQL Editor. Needed once, and again whenever a
Commissioner is added.

Verified 2026-08-31 that this is a hard boundary, not an inconvenience:
`select` on `app_commissioners` with the publishable key returns **401**, so
the seed genuinely cannot be automated with the credentials on this machine.

Remaining before remote authoring is live:
1. Seed `app_commissioners` (above). Until then, Postgres-backed reads and
   writes return nothing even for a correctly signed-in Commissioner, which
   blocks every step below that needs to be *proven* rather than written.
2. Prose repository cutover (sections table replaces `editorial/**/*.md`) —
   the last structural blocker; do a fresh export first.
3. Durable jobs cutover (`jobs` table replaces `_JOB`/`_JOBS` globals —
   `publish_jobs.py:41` and `sync_jobs.py:30`).
4. The hosted Desk also needs the Sleeper cache (12,225 players, rosters,
   matchups) in Postgres, plus resolution of **47** filesystem write sites
   (recounted 2026-08-31) across `desk.py`, `desk_editor.py`, `dossier.py`,
   `issue_builder.py`, `mailer.py`, `matchup_packet.py`, `packet.py`,
   `publish.py`, `publish_jobs.py`, `review_packet.py`, `site_build.py`,
   `storage.py`, which a read-only serverless runtime will reject.
5. Private Vercel project + env vars; add its URL to Supabase Auth only if
   magic links are added later (OTP does not need it).

## Remote authoring — Phase 1 foundation (2026-08-31)

**Status: remote access is NOT live yet.** Local Desk is unchanged and
remains the only working authoring path. What landed is the security and
recoverability foundation plus the architecture decision (docs/DECISIONS.md).

Recon numbers that drive everything: authoritative Commissioner state is
**247 rows + 14 meta + 38 prose files (57 KB)** = 1.9% of the DB; the
other 12,749 rows are rebuildable Sleeper/archive cache. Heaviest read
0.05s. **Prose is filesystem-authoritative** and **job state is process
memory** — those two are the only structural blockers to cloud hosting.

Landed and proven:
- `scripts/export_commissioner_state.py` / `import_commissioner_state.py`
  — full backup of authoritative state; round-trip verified byte-identical
  by checksum into a scratch restore. `backups/` is gitignored (it holds
  prose and manager context). **Run the export before any migration.**
- `leaguepage/auth.py` — magic-link sign-in, server-side allowlist, single-
  use login tokens, signed expiring session cookies, CSRF, rate limiting,
  no open redirect, kind-separated tokens (a login token cannot act as a
  session), allowlist re-checked on every request so removing an address
  kills live sessions.
- Central `RequireCommissioner` middleware in `desk.py`: every route is
  private by default; the ONLY public paths are `/health`, `/login`,
  `/auth/request`, `/auth/callback`, `/static/sortable.js`.
  `tests/test_auth.py` enumerates the live app and proves 30+ routes
  reject anonymous callers — a new route cannot be born public.
- `leaguepage/mailer.py` — one seam, `log` backend locally (writes
  `logs/login-links.log`) and `resend` backend for hosting.
- Env gates: `LEAGUEPAGE_AUTH_MODE` (off|required),
  `LEAGUEPAGE_COMMISSIONER_EMAILS`, `LEAGUEPAGE_SECRET_KEY`,
  `LEAGUEPAGE_MAIL_PROVIDER`, `LEAGUEPAGE_MAIL_FROM`, `RESEND_API_KEY`.
  Default `off` keeps the localhost Desk working exactly as before.

Remaining, in order (each is contained; see DECISIONS.md for rationale):
1. **Prose repository** — put `editorial/**/*.md` behind a repository so a
   Postgres backend is a drop-in. Content model is only 4 path shapes:
   `lowdown/lowdown.md`, `sections/<module>.md`,
   `matchups/<slug>/draft.md`, `proposals/<section>.md`.
2. **Durable jobs table** — replace the daemon-thread `_JOB/_JOBS` globals
   in `sync_jobs.py` / `publish_jobs.py` with DB rows the browser polls.
3. **Email proposals** — signed opaque section tokens, inbound webhook with
   signature verification + sender allowlist + replay protection, proposals
   stored separately and never auto-applied to Commissioner Content.
4. **Deploy** — second private Vercel project, Supabase Postgres, env vars.
5. Remote publishing stays optional and last; local Publish & Deploy works.

**Blocked on Jonathan (Claude cannot create accounts):** a Supabase project
(Postgres + connection string) and a Resend account + inbound domain. Until
one of those exists, no remote path can be finished.

## Tuesday prep routine (cloud, 2026-08-30)

- Routine **"League-Page Tuesday prep"**, id `trig_01BfY734Z6uagVJQbXkSJL2J`,
  cron `0 16 * * 2` (12:00 America/New_York while EDT; **after DST ends
  2026-11-01 it fires at 11:00 local** — update to `0 17 * * 2` then).
  Manage at https://claude.ai/code/routines (deletion is only possible
  there; this session's tooling cannot delete routines).
- **It runs in Anthropic's cloud, not on this machine.** It therefore
  cannot sync the local DB, touch the Desk, or refresh the local editorial
  workspace. Attaching the private repo was REFUSED by the platform
  ("You don't have access to a repository this routine uses"), so the
  routine has no repo and works purely from the public Sleeper API plus
  web news — no private material leaves the machine, by construction.
- What it delivers each Tuesday: the Wednesday-evening deadline reminder,
  a post-mortem of the completed week, previews of the upcoming week,
  a per-team news sweep with sources, and factual award candidates. It is
  explicitly barred from writing prose in Jonathan's voice, publishing,
  and printing handles/real names.
- The local half of the workflow is unchanged and is what the reminder
  points at: launcher → SYNC SLEEPER → EDIT WEEK N. The Desk's ghost
  briefs remain authoritative; the cloud pack is extra ammunition.
- **Delivery: a mobile push.** The first test run sent one unprompted, so
  `allowed_tools` is evidently not a hard block; the prompt now requires
  exactly one push (the reminder plus a one-line summary) as its last
  step, and a hard rule forbids every other outbound channel: no email,
  no calendar writes, no Drive, no messaging anyone else, even though
  Gmail/Calendar/Drive connectors are attached by account default.
- Verified test run 2026-08-30 (`cse_017eMhgWf2XTJ2k3qCo9dswQ`): success
  in 240s / 36 turns. It correctly refused to invent a post-mortem or
  awards because the season opens 2026-09-09, produced both leagues'
  Week 1 pairings using team names with "Roster N" fallbacks, swept ~25
  players for dated, sourced injury news, and pushed the reminder.

## Author-feature rule + dark Surfeit theme (2026-08-30)

- **The author does not headline his own newsletter.** `League` gains
  `author_roster_id` (1 in both leagues — a publication fact; Sleeper's
  `is_owner` also flags co-commissioners, so it cannot be derived).
  `matchup_interest.author_matchup_stakes` allows FEATURE only with real
  playoff consequences: 4+ weeks played, within 3 weeks of
  `playoff_week_start`, and a team on the cutline (seed spots±1) or both
  holding berths. Otherwise `feature_blocked` is set and
  `recommend_prominence` hands FEATURE to the next matchup; his stays
  MAJOR. Matchup Lab shows the reason; commissioner override still wins.
  Playoff shape is read from league settings, never hardcoded.
- **Surfeit is now dark.** Palette taken from the HAF A5 Skunk Works
  Futures coin/badge Jonathan supplied: night navy `#071a2f`, coin gold
  `#facd00`, sky `#58b6f0`, steel `#1d3d63`, silver-blue `#9fb6cc`.
  Contrast measured on production-equivalent build: body 8.4:1, gold
  headings 11.5:1, links 14.7:1, REACH 8.7:1, STEAL 10.2:1. Masthead now
  uses the transparent roundel; `_theme.html` (legacy renderer) kept in
  step. Disco unchanged and still distinct (slate + amber vs navy + gold).

## Browser-only weekly workflow (2026-08-30, final tranche of the day)

- **SYNC SLEEPER button** on Desk home: `leaguepage/sync_jobs.py`, same
  async-job pattern as publish (POST returns instantly, polling UI,
  duplicate clicks join). One job = both leagues' Sleeper sync + snapshots
  + transaction contexts + automatic `refresh_issue_research` for every
  existing current-week workspace — the Build step is folded in, so
  briefs are fresh right after sync. Runs in-process (no subprocess, no
  shell). Home shows Synced timestamp, per-league summary (week, teams,
  new transactions, renames), Show Sync Details, and failure keeps the
  successful league. Terminal sync is fallback only (README updated).
- **Prose protection**: the refresh path is the Build button's own logic —
  commissioner_notes.md never overwritten, content files only created
  when absent; guarded by test_refresh_preserves_commissioner_prose.
- **display_name everywhere humans read**: matchup analysis teams now
  carry `display_name` (override > Sleeper name > "Roster N"); Matchup
  Lab titles, matchup detail headers, story-candidate headlines, and
  angle premises use it. Slugs (`roster-N-vs-...`) remain the stable
  internal identity for paths/URLs by design and never churn on renames.
- 206 tests. Measured workflow: launcher → Sync (1 click, ~3s) →
  Edit Week N (1 click) → click a textarea (1 click) → typing.

## Calibration tranche (2026-08-30, after the feature tranche below)

- K/DST verdict: overall FantasyPros ECR ranks special teams below the
  draftable range (best K #202, best DST #186 on the 1QB board), so their
  huge "reaches" are reference artifacts. Headline Biggest Reaches/Steals
  are skill-position only; K/DST get a Special Teams Outliers box with
  within-position context and a disclosure. Per-pick rows unchanged.
  Disco's superflex board (no K/DST, QB #1 overall) confirmed correct.
- Draft reference snapshots are hash-pinned in tests/test_calibration.py:
  re-importing rankings mid-season FAILS tests by design. A new season's
  board belongs under a NEW source key.
- Transaction engine: surplus = usable depth (startable-quality players
  beyond lineup demand); questionable needs a materially bad drop side +
  one more wrong signal; medium confidence says "One plausible rationale";
  low collapses to "Rationale unclear"; move lines are type-aware
  ("Claimed X for N FAAB"); dropped players now get reference-derived
  values (a real bug: they read 0, disabling drop-side checks); curated
  Force Flow is ordered by editorial priority (trade > FAAB > weakness
  fix > shift > churn).
- Sortable tables remember per-page sort in sessionStorage (Back keeps a
  comparison). Ghost lowdown briefs flag repetition when the boldest
  pick/best value already appears in 2+ published issues.
- Browser-pane note: the screenshot compositor reliably fails on scrolled
  positions of these pages; the workaround is a tall emulated viewport
  (resize_window height ~2400) and screenshots from scroll position 0.

## Draft value + transaction rationale + sortable tables (2026-08-30)

- **Draft value**: `leaguepage/draft_value.py` classifies every pick against
  the stored FantasyPros ECR snapshot (delta = pick_no − ref; negative =
  early). One full league round (total_teams, read dynamically: 12 Disco /
  10 Surfeit) = REACH/STEAL; ±2 picks = on board; between = EARLY/VALUE
  (shown as signed numbers, not labels). Visual intensity =
  |delta|/league_size capped at 3 rounds (`--dvi` CSS var + color-mix);
  words always carry the meaning. Surfaces: Draft page (Market Deviations
  top-5s, per-team tables, full board), Team page Draft Recap (biggest
  reach/steal + per-pick), ghost briefs (draft-capsules get reach/steal
  counts + consensus-following/defying tags). Methodology note on both
  pages says market value, not a draft grade.
- **Transaction rationale**: `leaguepage/transaction_analysis.py` infers
  plausible roster logic (weakness / depth / injury-drop / surplus /
  rebalance / streaming), labels no-story moves "Rationale unclear", and
  "questionable" only on ≥2 wrong-direction signals with no positive
  rationale. Wording is always "Likely/Possible rationale" — never manager
  intent; confidence is internal-only. Before/after positional ranks are
  persisted at sync (`txn_ctx:{league}:{txn_id}` meta,
  `record_transaction_contexts` called from scripts/sync.py; render omits
  the delta when no trustworthy context exists). Post-move outcome (starts/
  points since) is separate and never rewrites the original reading.
  Surfaces: Force Flow "Reading the Moves" + full log, Team page Key
  Moves, matchup + forceflow ghost briefs, story candidates
  (weekly_signals). FAAB now reads settings.waiver_bid (the old
  waiver_budget-only sum missed all waiver claims).
- **Sortable tables**: `static/sortable.js` (vanilla, progressive; served
  to the Desk at /static/sortable.js) — opt in with `<table data-sortable>`,
  th `data-sort-type`/`data-sort-dir`/`data-nosort`, td `data-sort-value`.
  Click cycle asc→desc→canonical; missing values sink; stable ties;
  keyboard + aria-sort. Sortable: teams matrix, standings (+under-hood,
  playoff via defaults), draft tables, team Draft Recap, Force Flow log,
  Desk team-names panel. Deliberately fixed: issue prose, curated
  editorial sections, the Desk power-ranking form (authorial order).
- 188 tests passing. Real-data checks: Jahdae Walker (Disco) = REACH ·
  244 picks early; Jordan James (Surfeit) = REACH · 131 early; Surfeit
  QB sort puts Los Bandidos (#1) first.

## GitHub + Vercel Git integration (2026-08-30)

- Source is hosted at **https://github.com/Biomusician/league-page-v2**
  (PRIVATE). Legacy public fork `Biomusician/league-page` (2022 Svelte
  project) is unrelated; never touch it. Local remote `origin` points at
  league-page-v2.
- Vercel project `league-page` is connected to that repo, **production
  branch `site`**. `site` carries only the audited `dist/` output;
  `scripts/push_site_branch.py` builds, audits, commits, pushes it, and a
  push auto-deploys production (proven: dpl_AzUYd7Hybjg71o4uuWV3cRSdPxYv).
  `vercel.json` on both branches disables `main` deployments so the source
  tree can never be built or served by Vercel.
- Vercel cannot rebuild the site (the build reads the private local DB);
  the audited artifact IS the deployment unit. Do not "fix" this by
  committing private inputs.
- The Desk's Publish & Deploy still uses the direct CLI path (Option A);
  the Git path is a proven alternative, not yet wired into the Desk.
- **`main` is pushed and tracks origin.** Before the push (Jonathan's
  call, 2026-08-30) the local history was purged with git-filter-repo:
  `commissioner_notes.md` and `PREP.md` stripped from all commits and two
  handle strings replaced in old blobs; commissioner notes are now
  local-only files like managers.json. Full pre-rewrite history lives in
  `League-Page-pre-github-history-2026-08-30.bundle` (repo root,
  gitignored, never push it). `scripts/audit_repo_privacy.py --history`
  must be clean before any future main push; it audits source history
  only (`site` is audited at build time, and handles in `archive/` are
  verbatim-public).
- Vercel credentials: real login done by Jonathan 2026-08-30 (`npx.cmd
  vercel login`). Claude sessions see a phantom overlay copy of
  auth.json; only Jonathan's own terminal counts (see DEPLOY.md).

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
- Logos (from Jonathan): static/disco-logo-banner.jpg (dark masthead +
  root card), static/disco-logo-light.png (unused, kept for light
  contexts), static/surfeit-badge.png (Skunk Works roundel, transparent
  background for the dark theme; masthead + root card),
  static/surfeit-logo.jpg (superseded white-background version, kept).
  build_site copies static/ -> dist/assets/.

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
- **Ghost-brief authoring model (Jonathan, 2026-08-30, canonical)**: empty
  sections start as a private ghost writing brief (leaguepage/
  ghost_briefs.py — strongest facts, angles, 0-2 callbacks, structure;
  ~relevance-filtered, computed live from synced data at page load, richest
  for weekly matchups via compute_week). First keystroke replaces the
  ghost; emptying restores it; the brief stays available under Writing
  brief / Show evidence. Ghost text is never content: it cannot save,
  snapshot, publish, or count as written (tested). Claude prose is
  OPT-IN via Request Claude draft / Request rewrite -> proposals reviewed
  side by side. Provenance migration 2026-08-30: unaccepted Claude-ROUGH
  files (surfeit draft custom/hardware/draft-capsules, surfeit week-01
  TEST matchup drafts) moved to proposals/ (matchup files as
  proposals/matchup--<slug>.md), snapshots kept in revision history;
  commissioner-authored and published prose untouched.

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

## Publish pipeline (job model, 2026-08-30)

Publishing from the editor is asynchronous: POST publish-start returns in
under a second, a daemon thread runs snapshot -> build/audit -> (deploy ->
verify), and the publish page polls publish-status, showing each stage
live. One job per issue at a time (duplicate clicks join the running job);
every child process runs stdin-closed with explicit timeouts and
tree-kill (`npx --yes vercel@latest ... --yes`); a failed or timed-out
stage stops the pipeline, is shown with Show Publish Details (log tail),
and never reports success. Logs: logs/publish-{league}-{issue}.log
(gitignored, credential-redacting). The deploy outcome lives separately
from the snapshot lifecycle in meta deploy_state:{league}:{season}:{issue}
("published" still means only "snapshot frozen locally"). Root cause of
the old hang: one synchronous POST held the browser through the whole
pipeline (30-90s, indefinitely if npx ever prompted on stdin) while the
publish actually succeeded, inviting double deploys.

## Comments (giscus boundary, not yet activated)

Published native issue pages can carry a giscus (GitHub Discussions)
comments section: config in leaguepage/config.py COMMENTS (all values
public client-side config; empty repo = disabled, the current state).
Per-issue thread identity is data-term "league:season:issue" (stable
across domain moves); imported historical archive pages never get
comments; per-issue opt-out via disabled_issues. Graceful failure: the
issue renders fully if giscus is down. Moderation happens in GitHub
Discussions on the comments repo. ACTIVATION (Jonathan, one-time): create
a PUBLIC comments-only repo (never the private source), enable
Discussions, install the giscus app on it, copy the four values from
giscus.app into COMMENTS, rebuild + deploy. Note: commenters need GitHub
accounts.

## Team analytics layer (2026-08-30)

leaguepage/team_analytics.py: deterministic positional strength (per-league
lineup demand read from the Sleeper payload; greedy optimal-lineup fill,
starters 0.7 / depth 0.3, fragility + surplus; preseason consensus-rank
values transitioning to a labeled in-season blend at 3+ played weeks),
recent form (last-3), meaningful streaks, transparent Monte Carlo playoff
outlook (observed scoring ONLY - no consensus ranks; too_early < 3 weeks,
bands < 8, percentages after; remaining schedule beyond sync simulated as
random pairings, disclosed), weekly analytics snapshots persisted by
scripts/sync.py (meta analytics_snapshot:{league}:{season}:{week}; week 0 =
preseason) so deltas are historical fact, and key-move detection. Public
surfaces: Teams comparison matrix, per-team Positional Strength +
Trend/Outlook/Key Moves, Standings movers/hot/trouble/Playoff Picture, all
with concise methodology notes. Editorial integration: matchup ghost briefs
carry ROSTER CONTRAST + recent shifts, the lowdown brief carries league
shift lines, and weekly_story_candidates gains analytics candidates
(playoff swings, movers, streaks, record/all-play divergence). Nav order:
Home, CTP, Peer and Near-Peer, Force Flow, Draft, Black Box, Standings,
Teams, Archive.

## Team identity (2026-08-30)

resolve_public_names precedence: commissioner override > Sleeper team name >
neutral Roster N; a neutral "Roster N" override yields automatically when a
manager sets a Sleeper name (that is how Los Bandidos surfaced); login
handles never become public names. The editor Team names panel shows the
Sleeper name, PRIVATE manager context (owners, co-managed flag, round-1
slot, top players), rename detection, and per-row "use Sleeper name"
(deletes the override; deliberately no bulk button so real overrides cannot
be mass-destroyed).

## Voice (authoritative)

.claude/skills/my-writing-style/SKILL.md — supplied by Jonathan, installed
verbatim, never regenerate. Weekly/draft authoring workflows are explicit
drafting requests (drafting override). style_check.py is warnings-only;
the skill-level sweep is authoritative.

## Top post-MVP tasks

See POST_MVP.md. Short version: real names for the 8 neutral rosters,
finish the Draft Issues, preseason Peer and Near-Peer, preseason Takes,
custom domain + one-command deploy.

## Week 1 authoring tranche (2026-09-04)

Shipped, tested and deployed. `main` is at cba4054, the `site` branch at
91eefdf, and the live pages are byte-identical to the local build.

**Authoring.** Weekly sections start INCLUDED; only the three opt-in
sidebar features (`OPT_IN_MODULES`) start out. An included section with
nothing in it carries an amber "No meaningful material this week" chip
instead of disappearing. Matchup previews are real children of Common
Tactical Picture — in `module_states`, in readiness, in the approval gate,
in research routing, and in the editor DOM — and the parent summary reads
`3 / 5 approved`. Bold/Italic are buttons and Ctrl/Cmd+B/I on every prose
box, wrapping the selection in asterisks and unwrapping it on a second
press; the source stays Markdown and no editor library is loaded. "Request
Claude draft" is now "Copy prompt for Claude": it calls no API, opens no
rewrite request, and the prompt names the brief, the proposal path and the
writing skill rather than pasting any of them.

**Matchup research** (`leaguepage/matchup_research.py`) answers what a
preview is made of, per team, both sides: who decides it, who might not
play, what each has to get past, what they just did, what they could still
do, what is on the record against them. Byes and projections are declared
unavailable rather than inferred — the synced player payload has no bye
week and Sleeper publishes no projections. Roast ammunition uses the
project's own reach classifier (a full round or more early) on skill
positions only. History appears only when the last meeting was notable.
All of it is private: it is computed live, ships in the packet as
`research.md`, and reaches a reader only where he writes it himself.

**Known limits.** No bye weeks and no projections, for the reason above.
`_matchup_brief` is the only consumer of the research module; the AUTHORING
packet embeds its output rather than calling the functions directly.

**Next.** Week 1 is drafted and approved section by section on the Desk;
publication is the Commissioner's act and was deliberately not performed.

## Weekly model, Force Flow, provenance and identity (2026-09-04)

**The weekly post is Lowdown → Matchups → Custom section(s) → Weekly
Hardware.** Hardware is always last (`WEEKLY_ORDER` / `_order_rank` in
`leaguepage/issue_builder.py`). Every recurring section starts INCLUDED and
NOT APPROVED; `OPT_IN_MODULES` is now empty. A section with nothing in it
advises exclusion and does not exclude itself.

**Custom sections are repeatable and needed no schema change.** Keys are
`custom`, `custom-2`, `custom-3`; the row is the section, so nothing exists
until `+ Add custom section` writes one. Rename in place; exclude rather
than delete. Existing prose did not move.

**Retired as future authoring concepts**: `forceflow`, `all-city`,
`all-city-marquee` (`RETIRED_MODULES`). Never offered on a new issue; an
issue with a saved included row keeps it, publishes it, and shows it under
Administration rather than on the checklist. Historical snapshots are
untouched.

**Force Flow is a standing league page**, at `/{league}/transactions/`,
already in the public nav. `leaguepage/force_flow.py` adds a private
flagging layer over `transaction_analysis`: unusual FAAB (league-relative,
from the league's own bid distribution), free starters, notable drops,
churn, trades, hard-to-read moves, and possible blocks. Every flag carries
evidence; inferred flags say so. Review and optional Commissioner notes at
`/commissioner/{league}/{season}/force-flow`. Nothing publishes on its own.

**AI provenance** (`leaguepage/provenance.py`, table `prose_provenance`) is
structural: generator + input class + hash of what was accepted. Fully
generated and unedited content carries a small caption before it; one
edited character removes it. `method` is a fixed vocabulary, so no path or
prompt can be stored or rendered. Deterministic output says "generated
automatically" rather than naming a model.

**About** edits at `/commissioner/site/about`, saves to
`editorial/site/about.md`, publishes to `/about/`. Not a module: it cannot
enter weekly readiness or block an issue. `config.SUPPORT_URL` is still
empty by design, so no donation link renders.

**Identity**: `leaguepage/identity_audit.py` reconciles owners across the
four stores. Live result: no blockers; two warnings, Disco roster 4 and
Surfeit roster 3 have renamed themselves on Sleeper since their public
names were confirmed. **Seebass** is canonical for The Surfeit.

**Known residue.** The superseded spelling remains in the *published* draft
issue (`editorial/2026/surfeit/draft/sections/custom.md` and the
`published/surfeit/2026/draft.r3.json` snapshot it produced). Correcting a
published issue is the Commissioner's act via
`scripts/identity_correction.py`, which keeps the original and adds a
revision; it was deliberately not done here.

**Next.** Week 1 remains unpublished; publication is his act.

## Commissioner override of generated content (2026-09-04)

**The rule.** Public prose is the Commissioner's and can be changed from the
card it appears on. Computed results are shown beside it and are not editable
as prose. Automation supplies the default; it does not remove editorial
control.

**Approval now follows content.** `editor_save` used to carry a comment saying
approval must be re-asserted and a `pass` under it, so a section could publish
text nobody had signed off under a green "approved" chip. Editing, restoring a
revision, accepting a Claude proposal or taking the generated copy all retire
the approval, mark the card `changed since approval`, and require re-approval.
Editing a matchup preview also unapproves Common Tactical Picture, because CTP
publishes the previews and has no text of its own. The mark is a `meta` row
(`approval-stale:<league>:<season>:<issue>:<section>`), cleared when he rules
either way.

**Weekly Hardware** (`leaguepage/section_defaults.py`). Decided awards and
their computed basis appear read-only under "Computed evidence"; the private
award note is shown on the Desk and never enters composed copy. `Use generated
copy` / `Reset to generated` composes the section deterministically from those
results, snapshotting his current text to History first. Provenance for that
copy is recorded as `provenance.DETERMINISTIC`, which renders as "generated
automatically" with an AUTO mark rather than an AI badge.

**Common Tactical Picture** gained an optional Commissioner intro
(`sections/ctp.md`), publishing above the previews. It never blocks approval or
publication, it does not publish when no preview is approved, and no second
copy of the matchup content exists anywhere. Writing an intro removes CTP's AI
badge even when every preview is untouched.

**Peer and Near-Peer** already read `sections/power.md` into the published
section and had no editor for it at all. It now has the same blurb editor.

**Retired sections an issue still carries** (Disco Week 1 has Force Flow) now
get a full editor under Administration, because prose that still publishes is
still his.

**Audit — can he change the prose from this screen?** Machine-checked across
both leagues, both issue types; `PUBLISHES PROSE WITH NO EDITOR: none`. The
test `test_every_weekly_card_with_public_prose_offers_an_editor` holds it.

| Module | Publishes prose | Edit path |
|---|---|---|
| lowdown | yes | editor, preview, history, approve, rewrite, reset to Claude draft |
| ctp | yes (its previews) | each preview editable, approved or not; optional parent intro |
| matchup:* | yes, inside CTP | editor; editing unapproves it and CTP |
| custom, custom-N | yes | editor |
| tracks, fades, blackbox, false-assumptions, draft-capsules | yes | editor |
| power | yes (blurb + ranking) | blurb editor; ranks, tiers and notes as fields |
| hardware | yes | editor + computed evidence + generated default + reset |
| forceflow, all-city\* (retired, still carried) | yes when included | editor under Administration |
| masthead | **no** — issue metadata; contributes nothing to the published page | none, and the card says so |
| intel, branches | **no** — self-omit until the scenario engine exists | none, and the card says so |

**Deliberately not editable as prose**, being computed evidence: award results
and their basis, matchup scores and margins, standings, power-ranking positions
(set as fields, not text), FAAB and transaction figures, playoff leverage.

**Verified on real data** against copies of `data/league.sqlite3` and
`editorial/` (his Desk on 8026 untouched): editing an approved Disco matchup
unapproved it and CTP; a CTP intro published above the previews; Force Flow's
retired prose edited and saved; History kept the prior text. The generated
Hardware control does **not** appear on today's real data and correctly should
not: Week 1 is unplayed, every matchup scores 0.0, so no award has nominees and
there is nothing to compose. That path is exercised on synthetic data in
`tests/test_commissioner_override.py`.

**Publication state at the end of this work.** Jonathan published **Disco
Week 1** himself from his own Desk at 2026-09-05T01:39Z, while this tranche
was being built; that snapshot is `published/disco/2026/week-01.json` and was
produced by the code as it stood before these changes. **Surfeit Week 1 is
still unpublished** and publishing it is his act. Nothing here published
anything, and his Desk process on 8026 was never restarted.

**His Desk needs a restart to see this.** Jinja reloads templates per request
but Python is loaded once, so a Desk started before these changes renders the
new markup against the old card data. Every new field is guarded, so the page
degrades to its previous behaviour rather than breaking — but the new controls
will not appear until the process is restarted.

## Draft page redesign, Force Flow team-first, archive boundary (2026-09-04)

**Starting state.** HEAD `2db7e89`, one commit ahead of `origin/main` (the
previous tranche's commit had not been pushed). Working tree carried
Jonathan's own Disco Week 1 publication (`published/disco/2026/week-01.json`)
and his typo fix in the `love-sutton-brocks` preview; both were left exactly
as found and are not part of the implementation commit.

**Draft page** (`templates/public/draft.html`, `_site_css.html`,
`base.html`). Wide container (`main.wide`, 84rem) via a `main_attrs` block
the prose pages do not use; facts strip; recap callout; Market Deviations
with reaches/steals side by side from 1024px and special teams spanning
beneath (or beside a lone list); two-column team tables; capped prose. The
build passes `draft_meta` alongside the old `status_line`. Off-board picks
excluded from headline reaches (`draft_value.headline_deviations`).

**Force Flow** (`templates/public/transactions.html`,
`leaguepage/force_flow_history.py` new). Team-first on every surface; trades
show `A ↔ B`; log columns Week · Team · Move · Added · Dropped · FAAB. Moves
That Mattered is assembled from story decisions × transaction rows on
`story_candidate_id` (shared with the Desk via `weekly_signals`), grouped by
week with the issue as provenance; Commissioner notes come from
`force_flow_notes` or the decision note; rationale is labelled as inference;
"How it aged" retained. Prose fallback only when an issue has Force Flow
prose and no structured selection.

**Archive.** `PERSISTENT_TAB_MODULES = {"forceflow"}`; `_issue_ctx` omits it
from every rendered issue and from the home page's "In this issue". Snapshots
untouched (hash-checked in `test_the_snapshot_is_not_rewritten_to_achieve_that`
and against the real Disco Week 1 file).

**Measured in the browser** (Chromium pane, static server on 8090 serving
`dist/`; `.claude/launch.json` added for it). Draft page at 320/375/430/768/
1024/1440/1920: `scrollWidth == clientWidth` at every width; `main` 1344px at
1440 and 1920; deviations 2 columns at ≥1024, 1 below; team grid 2 columns at
≥1024; all 13 tables inside `.tablewrap` and scrolling internally on phones;
jump links 44px; zero interactive targets under 44px. Force Flow at 320/375/
768/1440/1920: no overflow; team links 44px after the hit-area fix. Archived
Disco Week 1 at 1440: seven sections, no Force Flow. Screenshots were taken
and inspected at 1920 (both leagues' drafts, Force Flow), 375 (Disco draft,
Force Flow) and 1440 (archived Disco Week 1).

**Desk staleness.** No launcher-side stale-process fix has landed in this
repo; a Desk started before a Python change keeps the old code until it is
restarted. Public-site changes are only visible after build + deploy.

**Claude usage.** `claude-usage` (run via its own venv under `CC and ChatGPT
Teaming`) reports a healthy collector with no pool ever captured; its
consumer rule is "task fitness first; no budget telemetry, so shape nothing
by budget." Scope was not shaped by budget.

**Surfeit Week 1 remains unpublished.** Nothing here publishes.

**Gate and state at close (2026-09-04).** 1132 passed, 2 skipped (baseline
1099; 33 added). Build 102 pages, built-output privacy audit clean;
publication QA 3 issues, 0 blockers, 4 pre-existing warnings; repo privacy
audit clean at HEAD; the full-history audit's only finding is the
long-public commit `4c24977a` (a fake database URL in a test fixture,
already split at HEAD), which exposes nothing new. Implementation committed
as `561426e` on `main`; site deployed as `site @ 48a2b72`. Jonathan's Disco
Week 1 snapshot and his `love-sutton-brocks` typo fix remain uncommitted in
his working tree, untouched.
