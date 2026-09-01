# The All-City Team — feature editions

One JSON file per run. `leaguepage/all_city.py` validates and renders it;
`docs/DECISIONS.md` (2026-08-31) has the rationale.

There are two variants, and this directory holds the parent. The feature key
is the module key, so each variant is its own directory:

| Directory | Module | Rule |
| --- | --- | --- |
| `all-city/` | The All-City Team | Any incorporated city, any size |
| `all-city-marquee/` | The All-Marquee Team | The same, with a 100,000 floor |

Everything below applies to both unless the marquee README says otherwise.

## The rule

A player qualifies when his own first name or last name, standing alone, is
exactly the name of an incorporated municipality that its own state classifies
as a **city**. Towns, villages, boroughs, townships, CDPs and unincorporated
communities never qualify, at any size. No partial matches, no spelling
variants, no nicknames, no team or stadium names. Hyphenated and compound
surnames count as one name (Smith-Njigba is not Smith). Generational suffixes
are stripped before matching.

Countries in play are the United States, France, the United Kingdom and Sweden.
The default rule is the U.S. one; reach for an allied city only when no U.S.
city carries the name.

Tier is the census count and nothing else, and the validator enforces it:

| Tier | 2020 census population |
| --- | --- |
| Marquee City | 100,000 and up |
| City | 5,000 to 99,999 |
| Technical Qualifier | under 5,000 |

## Running it again

1. Copy the newest edition to `<season>-<issue_key>.json`.
2. Change `edition`, `issue_key`, `compiled_at`, and the `sources` retrieval
   dates. Editions bind to exactly one `(season, issue_key)`, so the previous
   run keeps publishing exactly what it published.
3. Rework `starters`, `bench` and `near_misses` against the current board. The
   valuation sources already in the repo are `refdata/adp/*.json` (FantasyPros
   expert consensus, re-imported by `scripts/import_adp.py`) and the synced
   Sleeper player index in `data/league.sqlite3` (position, NFL team, active
   status, depth-chart order, injury designation, and `search_rank` as a second
   independent consensus signal). Verify every city claim against a citable
   source and record it in the entry's `sources`.
4. Include the module for the issue on the Desk, write
   `sections/all-city.md`, approve, publish.

`validate_edition()` will refuse a lineup that is missing a slot, doubles up a
slot, repeats a player, names a non-city, or files a tier that disagrees with
its own population. Run the checks with:

```
.venv/Scripts/python.exe -m pytest tests/test_all_city.py -q
```

## What is public

Only the fields in `all_city.PUBLIC_ENTRY_FIELDS` render. `evidence`,
`sources`, `consensus` and `research_notes` are local review material and never
reach `dist/`; `bench` never renders at all. Put working notes in
`research_notes` freely.

## Table columns

`columns` picks what the table prints, from `pos`, `player`, `city`, `class`,
`population`, `verdict`; `pos`, `player` and `city` are mandatory. The default
shows the qualification tier. An edition where every row is the same tier
should show something that actually varies, which is why the marquee edition
prints population instead.

## Expansion

`roster_format` in the edition drives the lineup check, so FLEX, DST or a full
bench is a data change plus adding the position to `all_city.KNOWN_POSITIONS`.
A new RULES variant is a new directory plus one line in `MODULE_DEFS`.
Nothing else in the pipeline needs to move.
