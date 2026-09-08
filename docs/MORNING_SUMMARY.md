# Morning summary — 2026-09-08

Overnight product tranche. Started at `2111db9`, five commits, tree clean.
Nothing published, nothing deployed, no cutover, Supabase untouched.

---

## What changed while you slept

**The Issue Room was rendering its section cards with none of their CSS.**
`_section_card.html` and `_matchup_card.html` are shared by five Desk
screens, and their whole stylesheet lived inside `editor.html` — so the one
screen the Desk home *doesn't* link to was the only one that was styled.
In the Room that meant 41 status chips rendering as plain 16px body text,
a writing box 191px wide in Georgia, and 2,661 characters of your **private
writing brief** printed into the card as visible copy, because the rule
that hides it was in the other file. It is now a shared partial and the
brief is hidden again.

**Home and the Room disagreed about whether the week was done.** Home said
"0 would block publish"; the Room said "BLOCKED · 8"; both were describing
the same issue in both leagues. Home was skipping the calculation entirely
for a published issue. They agree now, and Home says whether the week has
shipped.

**"Next" said "Sync Sleeper" to both leagues for two days** while eight
sections sat unapproved. Staleness was the first rung, and your data is
over twenty hours old on any day that isn't the day you synced. It now
outranks the work that *reads* the data and not the work that doesn't.

**Opening the review screen was modifying a git-tracked file.** That GET
called `build_review_packet`, which writes. It was also where "Next" sent
you. Reading and writing are separate now; writing it is a button.

**The issue builder could approve a section the editor would refuse** — and
sign it, so afterwards it looked audited. Both screens ask the same gate
now.

**The link you paste in the league chat** said "Week 01 of DISCO CHAT." It
now carries your own opening sentence.

**"This week in 30 seconds" was below the fold** at y=904 against a 900px
fold, because the front-page excerpt was taking the Lowdown's *second*
paragraph, and that paragraph is the note about ChatGPT and Vercel. Now
y=799, with your lede fully intact.

---

## What will feel different

- The Room's writing box: **405px → 672px** at 1440px, and no longer stuck
  at 405px on a 1920px screen. About 70 characters instead of 40.
- **"Approve all ready" is in the Room** — 3 clicks instead of 16. It was
  already loaded; it just had no button.
- The rail stops saying "needs review" to eight of eleven sections. On
  Disco it now says **"approved, unsigned"**, which is the actual state and
  the one-time re-approval `CUTOVER.md` describes.
- **Week navigation exists.** Week 2 used to need a hand-edited URL.
- The **Command Brief** and **Force Flow** are linked from Home and the
  Room. Force Flow previously had no inbound link anywhere in the Desk.
- **Takes** is reachable from where you write — one `{% include %}`; the
  context was already there.
- Card buttons have weight now: Approve is primary, the four that discard
  or displace written work are outlined in the accent, reference recedes.
- Public nav carries plain-language subtitles under the branded names, and
  becomes a tappable scrolling strip on a phone (44px targets, was ~18px).
- The issue page has a heading, a contents list above the fold, and
  prev/next. My Team jumps to just under the lede once you've picked a team.

---

## What I deliberately did not touch

Your prose. Published snapshots (`published/`, `editorial/` and `archive/`
are byte-identical — git says clean). Publication state. The cloud cutover,
`.env`, Supabase, `LEAGUEPAGE_PROSE_BACKEND`. No deploy.

Also deferred on purpose: the public reading measure (changes bytes readers
see, and is a no-op on phones); the Desk token layer (its chip rename
collides with three places `desk-editor.js` uses `.approved` as state); and
the 15 native `alert`/`confirm`/`prompt` calls.

Two reviewer proposals were **dropped** for colliding with "don't remove
functionality": deleting the Story Board, and stripping Approve from the
Matchup Lab. The second turned out to be the only thing that writes the
repetition log — removing it would have left Story Memory saying "this bit
is fresh" forever. It's relabelled instead: **"Mark preview done"**.

---

## What needs your eyes

1. **The AI-disclosure paragraph is no longer on the front page.** It's the
   second paragraph of the Lowdown, and trimming the excerpt to clear the
   fold drops it. It's still in the issue, one click away, and I didn't
   rewrite a word — but if that disclosure belongs on the front page it
   needs a home, and `/about/` is still the stub
   *"Information about the project will be added here."* **Your call.**
2. **Nothing has been seen at a real 1440px or on a real phone.** Geometry
   was verified by DOM measurement, which is stricter than eyeballing, and
   the Desk home, the Issue Room and the public nav were checked as images
   at the pane's native ~800px. But the full-size look is still unverified.
3. **Two teams named in the live Week 1 issue no longer exist** ("George &
   Friends" is DIP's old name; his current team appears nowhere in the
   section that names teams). That's frozen published prose — correcting it
   is a republish and your act.
4. The issue page now has two `<h1>`s (masthead + issue title). Standard
   practice, but inconsistent with the other 100 pages.

---

## Look at these first

- `http://localhost:8031/commissioner` — the two league cards
- `http://localhost:8031/commissioner/disco/2026/issue/week-01/room` — the
  repaired Room; check the rail, the Takes tab and the writing column
- `http://localhost:8031/commissioner/disco/2026/force-flow` — previously
  unreachable and unstyled
- `http://localhost:8090/disco/` — fold, nav, My Team
- `http://localhost:8090/disco/2026/week-01/` — heading, contents, prev/next

Both servers were left running (`.claude/launch.json`: `desk-qa` 8031,
`static-site` 8090).

---

## Commits

| | |
| --- | --- |
| `235fa00` | Give the shared card styles to every screen that renders them |
| `a5c23fa` | Make the Desk give one answer to "is this issue done?" |
| `f718dca` | Give the reader the week before the machinery |
| `cd62499` | Make the Issue Room the room |
| (docs) | HANDOFF + this file |

Full detail, the before/after measurements and the complete deferred list
are in `docs/HANDOFF.md`.
