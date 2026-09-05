# How to compact a League Page session

Directions for summarising this project's context. `/compact` reads this;
so does an automatic compaction. Its counterpart is `.claude/COMPACT.md`,
which a SessionStart hook re-injects *after* compaction — that file says
what is always true, this one says what to carry out of the conversation.
Where they overlap, `.claude/COMPACT.md` wins, and the repo wins over both.

**Trigger.** Compact when context passes **75%**, after finishing the
current answer — never mid-task, never between a code change and its test
run. `autoCompactWindow` in `~/.claude/settings.json` is the backstop; the
value is 75% of the model's context window in tokens (currently `750000`,
which assumes a 1M window — change that one number if the window differs).

---

## Preserve verbatim

1. **Every hard prohibition** in `.claude/COMPACT.md`. Never paraphrase a
   prohibition; a softened rule is a broken rule.
2. **What is unpublished.** Week 1's publication status, and the fact that
   publishing is Jonathan's act. If a tranche said "do not publish", carry
   that sentence.
3. **Decisions waiting on Jonathan** — anything asked and not yet answered,
   and any assumption recorded that he has not confirmed.
4. **Exact state**: `main` SHA, `site` SHA, whether the tree is clean,
   whether the last deploy was verified in production.
5. **Working-session hazards**, because both have bitten:
   - He may have the Desk open on 8026 and be writing. Use a QA Desk on
     another port; do not restart his.
   - Never `git add -A`. Stage by explicit path. Editorial prose you did
     not deliberately author is his work.
6. **Residual defects and deliberate omissions**, with the reason. A known
   gap that loses its reason gets "fixed" wrongly by the next session.
7. **Verbatim requirements** from the current tranche that are still
   outstanding — the user's own words, not a gloss.

## Compress hard

- Tool output, test output, build logs. Keep the **counts and the verdict**
  ("1064 passed, 2 skipped"; "privacy audit clean"), drop the rest.
- File dumps and code you have already written to disk. The repo is the
  source of truth; a summary that re-quotes a committed file is wasting the
  window on something `git show` answers.
- Recon and red-team agent reports. Keep the **findings acted on**, the
  findings deliberately **not** acted on with the reason, and any fact that
  cost real work to establish (a shape, a threshold, a join key). Drop the
  narration.
- Draft prose already saved under `editorial/`. Never re-quote it.
- Superseded reasoning. Keep the decision and its rationale, not the path
  that reached it.
- Anything derivable: file structure, function signatures, what a test
  asserts. Name the file instead.

## Never carry

- Real Sleeper handles, display names, user ids. Public team names and
  roster ids are fine; handles are not, in any form, including inside a
  quoted tool result.
- Private research: roast ammunition, ghost briefs, possible moves,
  commissioner notes, matchup evidence lines.
- Contents of `editorial/managers.json`, `data/`, or `backups/`.
- Secrets, tokens, connection strings — including in an error message.

## Shape of a good summary here

Lead with **state**, not story: what is committed, deployed, unpublished,
and outstanding. Then the current tranche's remaining requirements in the
user's own words. Then decisions and their rationale. Then residual risks.

Facts this project keeps re-learning, so carry them when they are live:

- The canonical weekly post is **Lowdown → Matchups → Custom section(s) →
  Weekly Hardware**, and Hardware is always last.
- **Seebass** is the canonical Surfeit callsign.
- Force Flow is a standing league page, not a weekly section.
- Claude Code *is* the editorial AI. No LLM API key anywhere, ever.
- Deploy only the audited `dist/` artifact, via `scripts/push_site_branch.py`.
- `.venv/Scripts/python.exe` explicitly; three Pythons on PATH.

## After compacting

Re-read `docs/HANDOFF.md` before resuming substantive work, and re-check
`git status` and the current SHAs rather than trusting the summary's copy
of them. If the summary and the repo disagree, the repo is right.
