# Overnight product run — 2026-09-03

Working file for one long autonomous tranche. Findings came from six
read-only recon agents; everything below was verified in the code or the
real database before it was listed. Delete this file once the work lands in
HANDOFF.md and DECISIONS.md.

Baseline: `main` @ 34dc0c7, `site` @ cd496df, both level with origin,
532 tests passing, production live.

## Confirmed defects (recon, verified)

| # | Defect | Where | Status |
|---|---|---|---|
| 1 | Playoff sim reads `records[rid]["points_for"]`; `team_record` returns `fpts`. Every simulated team starts at 0 PF | `team_analytics.py:328`, `:375` | |
| 2 | `record_snapshot` standing order therefore degenerates to roster_id order; week-0 baseline manufactures phantom movement in week 1 | `team_analytics.py:375`, `:414` | |
| 3 | Preseason standings sort by (wins, fpts) = all zeroes, so `#11` is roster order rendered as a rank | `team.html:44`, `teams.html:10` | |
| 4 | FAAB read from `waiver_budget` (always empty; real bids live in `settings.waiver_bid`) at 4 of 5 call sites. FAAB Arsonist can never nominate | `team_analytics.py:448`, `weekly_signals.py:48`, `weekly_awards.py:242`, `matchup_analysis.py:174` | |
| 5 | Lineup efficiency column always renders `—` under a paragraph promising a number | `site_build.py:184` | |
| 6 | `late_season_leverage` weight defined, no component emits it, `Playoff Leverage` tag can never fire | `matchup_interest.py:22`, `:171` | |
| 7 | K/DST leak: `roster_contrast_lines` ranks all positions; `best_bench_swap` can nominate on a benched kicker | `team_analytics.py:532`, `weekly_awards.py:40` | |
| 8 | `recent_form` windowed all-play collapses every week to pseudo-week 0, cross-producting weeks | `team_analytics.py:258` | |
| 9 | Inbox "Add to Issue" reaches nothing for `change:*` ids — they are never in the candidate list the briefs read | `desk.py:79`, `issue_builder.py:347`, `:442` | |
| 10 | Every non-matchup candidate gets `magnitude=0.4`, `fresh=False`, no other signal: all score exactly 16 and sort alphabetically | `change_inbox.py:562`, `:580` | |
| 11 | Duplicate items for one fact (`change:standings` vs `analytics:standings`, odds, pos) | `change_inbox.py:523` | |
| 12 | Significance bands unreachable: a 3-seed upset scores 38 "Minor", a zero-FAAB trade scores 40 | `significance.py:27` | |
| 13 | Repetition penalty is per lane prefix, so one standings story last week penalises all twelve teams this week | `significance.py:151` | |
| 14 | Save for Later never re-surfaces | `change_inbox.py:471`, `:490` | |
| 15 | 82 of 98 public pages are dead or near-dead ends; team names unlinked in matchups/transactions/standings/draft/black-box | templates/public | |
| 16 | Zero `description`/OpenGraph/canonical on all 98 pages | `base.html` | |
| 17 | Team slugs fall back to `roster-N`, so the Game of the Week anchor is `#roster-10-vs-roster-11` | `site_build.py:205` | |
| 18 | The Commissioner's published draft ranking never reaches `/power/`, though the template already renders a model-vs-Commissioner gap | `power.html`, `site_build.py` | |
| 19 | `past_statement` receipt block renders on 0 matchup cards in both leagues | `matchups.html` | |
| 20 | Sync never fetches beyond the current week, so the playoff model cannot see a schedule that Sleeper already serves | `ingest.py:104` | |

## Confirmed capability unlock

Sleeper serves future-week pairings today. Proven in this database: surfeit
week 3 was fetched 2026-08-29, before week 1 kicked off, and came back with
complete `matchup_id` pairings and zero points. A real-schedule playoff
model is therefore not blocked on anything.

## Order of work

1. Analytical correctness (defects 1-8) — wrong numbers first.
2. Real schedule: ingest future weeks, real-pairing playoff sim, strength of
   remaining schedule, per-game leverage.
3. Public product: metadata and sharing, the link graph, Commissioner vs
   model on `/power/`.
4. Change Inbox 2.0 and significance recalibration.
5. Page rebuilds: matchup, team, transaction ageing, black box, draft ageing.
6. Mobile and accessibility pass.
