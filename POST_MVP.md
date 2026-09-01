# Post-MVP backlog

Prioritized. Nothing here blocks the live site.

1. **Real team names for the 8 neutral rosters** (surfeit 2/4/5/6/10, disco
   6/10/11). Currently "Roster N" via commissioner override. Rename on the
   Desk team-names panel, republish nothing; rebuild + deploy picks them up
   on data pages. Newsletter prose keeps its stricter gate.
2. **Complete Draft Issues.** Only the launch Lowdowns are published. The
   Surfeit hardware/capsule sections exist as ROUGH drafts
   (editorial/2026/surfeit/draft/sections/); Disco capsules not yet written.
   Review, edit, approve, republish the draft issues with more modules.
3. **Preseason Peer and Near-Peer.** Ranking rows exist for Surfeit but
   carry a blocking placeholder note; Disco not started. Set on the Desk,
   approve the power module, publish.
4. **Preseason Takes.** TAKE CANDIDATE lines from the rough Lowdown were
   not registered; track 5-10 per league on the Desk before Week 1 locks
   assumptions in.
5. **Custom domain + streamlined weekly deploy.** The production URL is the
   Vercel-generated alias; a custom domain is one dashboard action. A
   one-command sync->build->deploy script is worth adding once the weekly
   rhythm starts.

6. **All-City Team: FLEX and bench.** The 1QB/2RB/2WR/1TE/1K version ships
   in two editions, `editorial/features/all-city/2026-week-01.json` and the
   100,000-floor `all-city-marquee/2026-week-01.json`. Expanding to a full
   15-man roster is a data change plus adding the new slots to
   `all_city.KNOWN_POSITIONS`, because `roster_format` in the edition already
   drives the completeness check. Two things to settle first, and neither is
   code: DST would need its own rule (a defense is a team name, and team
   names are worth nothing under the current rule, so "Chargers" gets you
   Los Angeles and that is exactly the loophole the rule exists to close),
   and the bench dilutes the joke - the qualifying pool thins out fast below
   the top 60, so slots 8 through 15 are where "Technicality doing a lot of
   work here" stops being funny and starts being every row. Recommendation:
   add FLEX only, keep it to one slot, and leave DST and the bench alone
   until a reader asks for them. The marquee edition argues against expansion
   harder than the parent does: its qualifying pool is thin enough that the
   entire league contains two eligible kickers, so a bench would be padding.

Deferred further: archive full-text search on the public site, playoff
analytics, Branches and Sequels, False Assumptions automation, richer
team-history linking (current newsletter-mentions matching is thin
substring matching), additional visual polish.
