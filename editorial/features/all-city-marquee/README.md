# The All-Marquee Team — feature editions

The All-City Team with a population floor. Read
`editorial/features/all-city/README.md` first; everything there applies, and
only the deltas are here.

## What is different

**`rules.minimum_population: 100000`.** The city must have had at least 100,000
residents at the 2020 census. `validate_edition()` enforces it and refuses an
edition whose starters do not all clear it, or whose starters do not all record
a population at all.

**`columns` prints population instead of class.** Every row in this edition is
a Marquee City, so the tier column would say the same thing seven times.

**The allied-cities clause finally does something.** The parent rule reaches
for a French, UK or Swedish city only when no U.S. city carries the name. Here
it reads *no QUALIFYING U.S. city*: London, Ohio is 10,279 and fails the floor,
so London means Greater London. For an allied city the class test is whether
the place is a city under its own country's usage and administration, since
"classified a city by its state" is a U.S. construct with no counterpart
abroad.

**Washington runs on a named exception.** The sources genuinely conflict. The
Census Bureau's District of Columbia geographic guide records that the District
has one city, Washington, coextensive with it, and the standard list of U.S.
municipal corporations ranks Washington 22nd; the Bureau's population-estimates
glossary treats the District as a county equivalent and defines incorporated
places without it. That is a ruling, not a reading, so it is written into the
entry's `exception` field, marked `[1]` against the city in the table, and
footnoted underneath. The parent edition needs no such exception because
Washington, Pennsylvania (13,176) clears its rule on its own.

## Exceptions

`exception` on a starter is a **named, deliberate carve-out from the rule**.
It renders. Use it only where the rule genuinely does not reach and the call
is yours to make, and write it so a reader can disagree with it on the facts.
Anything that is merely an unusual-but-clear case (Hampton, Virginia being an
independent city, for instance) belongs in `research_notes` instead.

## Rerunning

Same procedure as the parent. One extra step: re-verify every population
against the census entry, because this edition's whole premise is the number.
Anything you cannot verify goes in `bench` with the figure left out and a
CHECK THE FIGURE note, never in `starters`.
