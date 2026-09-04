"""One-time migration: unwrap machine-wrapped editorial prose.

Line breaks in a section file used to mean nothing — Markdown folded a
single newline into a space — so prose written to disk by Claude Code was
hard-wrapped at about 78 columns for readability in a diff. Now a line
break means a line break (see leaguepage/prose.py), and those wraps would
render as ragged lines on the published page.

So the wraps come out, once, and stay out: editorial prose is stored one
line per paragraph and soft-wrapped in the editor.

A break is the wrapper's when the sentence runs straight through it: a
long line that did not finish a sentence, followed by one that carries on.
A break the author typed lands where a thought ends. And because a
paragraph is either wrapped or not, one certain wrap inside it settles the
rest — which is what catches the breaks where a sentence happened to end
exactly at the margin.

Headings, list items, bullets, table rows, quotes, HTML and code are never
touched, and neither is any paragraph with a short line in it, because a
short line is somewhere he stopped on purpose.

Run:  .venv/Scripts/python.exe scripts/reflow_prose.py [--apply]
Without --apply it reports what it would change. Either way it refuses any
file whose rendered page would move, comparing under the pre-migration
renderer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import markdown  # noqa: E402

EDITORIAL = REPO / "editorial"

# What publishes. `generated/`, dossiers, AUTHORING/PREP/README briefs and
# commissioner notes are working material and are not touched.
PUBLISHES = ("sections", "lowdown", "matchups", "proposals")
SKIP_NAMES = ("AUTHORING", "PREP", "README", "REVIEW_PACKET", "REVISION_REQUESTS",
              "AUTHORING_INDEX")
SKIP_DIRS = ("generated", "dossiers", "test-archive", "features")

# A line has to be long enough to have reached a wrap point before a break
# at the end of it can be read as the wrapper's rather than the author's.
WRAP_FLOOR = 58

_STRUCTURAL = re.compile(r"^\s*(#|[-*+]\s|\d+[.)]\s|>|\||```|~~~|<|\[\^|•|→|·)")
# A list item or bullet owns the wrapped lines underneath it, so it opens a
# block. A heading, table row or HTML line owns nothing; it only closes the
# block above it.
_OPENS_BLOCK = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|>|•|→|·)")


def _is_prose_line(line: str) -> bool:
    return bool(line.strip()) and not _STRUCTURAL.match(line)


# A line that ends here ended a sentence, so the break after it might be
# one the author typed.
_TERMINAL = ('.', '!', '?', ':', ';', '"', '”', '’', ')', '*', '_')


def _certain_wrap(line: str, nxt: str) -> bool:
    """Is the break between these two lines unmistakably the wrapper's?

    Only when the sentence runs straight through it: the line stopped near
    the margin without finishing a sentence, and the next one carries on.
    A break the author typed lands where a thought ends.

    Nothing here reads a wrap width, which matters — these paragraphs were
    wrapped near 78 and later edited in place, leaving lines of 67 and 143
    inside a single paragraph that no width rule would catch.
    """
    if not (_is_prose_line(line) and _is_prose_line(nxt)):
        return False
    if line != line.rstrip():
        return False          # trailing spaces are a Markdown hard break
    if len(line) < WRAP_FLOOR:
        return False          # short line: he stopped there on purpose
    return not line.rstrip().endswith(_TERMINAL)


def _reflow_block(block: list[str]) -> list[str]:
    """Join a blank-line-delimited block of lines.

    If one break inside a paragraph is provably a wrap, they all are: a
    paragraph is wrapped or it is not, and the wrapper did not stop halfway
    down. That inference is what catches the breaks where a sentence
    happened to end exactly at the margin, which no rule reading only the
    two lines either side can tell from a break he meant.
    """
    # The first line may be the list item the rest hang under; every line
    # after it has to be plain prose.
    if len(block) < 2 or not all(_is_prose_line(l) for l in block[1:]):
        return block
    if any(l != l.rstrip() for l in block):
        return block
    if not all(len(l) >= WRAP_FLOOR for l in block[:-1]):
        return block
    if not any(_certain_wrap(a, b) for a, b in zip(block, block[1:])):
        return block
    return [" ".join(l.strip() for l in block)]


def reflow(text: str) -> str:
    """Unwrap; leave every break the author typed exactly where it is."""
    out: list[str] = []
    block: list[str] = []
    fenced = False

    def flush() -> None:
        out.extend(_reflow_block(block))
        block.clear()

    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            flush()
            fenced = not fenced
            out.append(line)
            continue
        if fenced or not line.strip():
            flush()
            out.append(line)
            continue
        if not _is_prose_line(line):
            flush()
            if _OPENS_BLOCK.match(line):
                block.append(line)      # its continuation lines belong to it
            else:
                out.append(line)
            continue
        block.append(line)
    flush()
    return "\n".join(out)


def candidates() -> list[Path]:
    found = []
    for f in EDITORIAL.rglob("*.md"):
        rel = f.relative_to(EDITORIAL)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if f.stem.split("-")[0] in SKIP_NAMES or f.stem in SKIP_NAMES:
            continue
        if not any(part in PUBLISHES for part in rel.parts):
            continue
        found.append(f)
    return sorted(found)


def _old_render(text: str) -> str:
    """The renderer as it was before nl2br. If what this paints moves, the
    migration changed something that was already published correctly.

    Compared with whitespace runs collapsed, because that is what a browser
    does to the newlines Markdown leaves inside a <p>: joining two wrapped
    lines changes those bytes and changes nothing on the page. A file
    carrying a <pre> block is refused outright rather than compared this
    way, since whitespace is content in there."""
    html = markdown.markdown(text, extensions=["tables", "smarty"])
    if "<pre" in html:
        return "REFUSE:" + html
    return re.sub(r"\s+", " ", html).strip()


def main() -> int:
    apply = "--apply" in sys.argv
    changed, unsafe = [], []
    for f in candidates():
        before = f.read_text(encoding="utf-8")
        after = reflow(before)
        if after == before:
            continue
        if _old_render(before) != _old_render(after):
            unsafe.append(f)
            continue
        changed.append((f, before, after))
        if apply:
            f.write_text(after, encoding="utf-8")

    for f, before, after in changed:
        print(f"{'reflowed' if apply else 'would reflow'}: "
              f"{f.relative_to(REPO).as_posix()} "
              f"({len(before.splitlines())} -> {len(after.splitlines())} lines)")
    print(f"\n{len(changed)} file(s); rendered HTML identical under the "
          f"pre-migration renderer.")
    if unsafe:
        print(f"\nREFUSED {len(unsafe)} file(s) — reflowing them would change "
              f"the rendered page:")
        for f in unsafe:
            print(f"  {f.relative_to(REPO).as_posix()}")
        return 1
    if not apply:
        print("Dry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
