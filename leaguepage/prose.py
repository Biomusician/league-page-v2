"""How this product turns commissioner prose into HTML.

One function, because the rule it encodes has to be the same in the Desk
editor's preview, the full-issue preview, publication QA and the built
site. If the preview and the page disagree about what a line break means,
the preview is worthless.

The rule: a line break he typed is a line break he gets. Standard Markdown
folds a single newline into a space, which is right for prose you compose
in a text file and wrong for prose you compose in a box on a screen. He
writes stanzas, one-line verdicts and lists of names that are not
`<ul>` lists, and every one of those arrived on the page as a wall.

`nl2br` is how that is done, and it is the whole of it: no manual `<br>`
in the source, no `<pre>`, nothing for him to remember. It also means a
hard-wrapped paragraph now renders ragged, so editorial prose is stored
one line per paragraph and soft-wrapped in the editor. `scripts/
reflow_prose.py` did that migration once; the rule is in docs/DECISIONS.md.
"""
from __future__ import annotations

import markdown

# `tables` for the standings-style blocks, `smarty` for real quotes and
# dashes, `nl2br` for the line breaks he typed.
EXTENSIONS = ["tables", "smarty", "nl2br"]


def render(text: str) -> str:
    """Commissioner prose to HTML. The one renderer; use it everywhere."""
    return markdown.markdown(text or "", extensions=EXTENSIONS)
