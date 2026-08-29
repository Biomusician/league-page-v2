# Installing the voice profile in Claude Code

Two files, two decisions: where the skill lives, and whether you want it always-on.

## 1. Install the skill

**For all your projects** (recommended — you'll want it in both league repos and anywhere else you draft):

```
~/.claude/skills/my-writing-style/SKILL.md
```

**For just the portal repo:**

```
<repo>/.claude/skills/my-writing-style/SKILL.md
```

The directory name is the skill name and the file must be called `SKILL.md`. Claude Code discovers it from the frontmatter `description`, so it loads on demand when a task looks like drafting prose.

## 2. Add the always-on pointer (optional but worth it)

Skills are invoke-on-demand, which means Claude Code decides whether the task warrants loading it. For drafting work that's usually right; for a repo where most prose *is* league-facing, the pointer removes the guesswork.

Append to `~/.claude/CLAUDE.md` (or the repo's `CLAUDE.md` if you scoped the skill to the repo):

```
When drafting newsletters, power rankings, matchup previews, commissioner
announcements or rulings, portal UI copy, or any prose published as me:
first read ~/.claude/skills/my-writing-style/SKILL.md and follow it.
```

Adjust the path if you installed to the repo instead. Don't add the line twice — re-running an install shouldn't stack copies.

## 3. What it will and won't do

**It applies to** prose you publish as yourself: newsletter issues, rankings, previews, draft recaps, announcements, rulings, portal microcopy, empty states, notification strings.

**It does not apply to** code, code comments, commit messages, test fixtures, or documentation written in the project's voice rather than yours. If you want a house style for those, that's a separate and much shorter file.

## 4. How it stays current

The file carries its own update instructions. When a draft sounds off, say so — the rule is that Claude shows you the change before writing it, routes it to the right section (surface habit, tone shift, or a new don't), and edits in place rather than restructuring.

Two things it's told never to absorb: text other people wrote, and traits inferred from AI-drafted work. Both were live failure modes while building it.

## 5. Known gaps

- **No house style for the docs register.** Mechanics observed (numerals for figures, serial comma) but nothing formally recorded. If the league constitution needs one, say so and it gets added.
- **Draft-product specifics are inferred, not observed.** The profile knows your draft-recap voice from newsletter issues (the ranked bust/steal rundown, self-graded first). If the portal's draft tooling produces something structurally different — live draft-board commentary, per-pick blurbs, auto-generated grades — that's a new surface and worth a few real samples once you have them.
- **Two leagues, one profile.** It's told to keep in-jokes scoped to their own league, but it has samples from only one of them. The second league's running gags are invisible to it until you feed it a few issues.
