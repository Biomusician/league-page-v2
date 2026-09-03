"""Prose the gate used to block, and defects it used to miss.

Every blocker here has no override path, so a false positive stops
publication outright. A gate that blocks a good sentence gets switched off,
and then it is protecting nothing.
"""
from __future__ import annotations

import pytest

from leaguepage.pubqa import QAContext, check_sections


def _blockers(md: str, *, names=None):
    ctx = QAContext(league_slug="disco", season="2026", issue_key="draft",
                    n_teams=12)
    ctx.name_tokens = names or {}
    ctx.public_names = {rid: "" for rid in (names or {})}
    return [f.title for f in
            check_sections([{"module_key": "lowdown", "title": "Lowdown",
                             "content_md": md}], ctx)
            if f.severity == "blocker"]


@pytest.mark.parametrize("md", [
    "This was a classic man-vs-machine week for the whole league.",
    "It became an us-vs-them season pretty fast around here.",
    "Set it with `a**b` in the config file and move on.",
    "Playoffs are coming soon for this roster, and he knows it.",
    "He went 3-1 in weeks XXX through XXI of the old numbering.",
    "See the note.[^1]\n\n[^1]: methodology lives here",
])
def test_ordinary_prose_publishes(md):
    assert _blockers(md) == [], md


@pytest.mark.parametrize("md,expected", [
    ("The card at roster-6-vs-all-barkley-no-bite says otherwise.",
     "Internal identifier in prose (internal matchup slug)"),
    ("He looked at roster 4 and made the trade anyway.",
     "Unresolved roster placeholder"),
    ("## Preview\n\nComing soon\n", "Raw placeholder in prose"),
    ("![board](assets/board.png)", "Image source is not publishable"),
    ("Some prose here.\n\n    # This heading never rendered\n",
     "Markdown heading did not render"),
])
def test_the_real_defect_is_still_caught(md, expected):
    assert expected in _blockers(md), (md, _blockers(md))


def test_lowercase_roster_is_the_same_defect_as_capitalised():
    """The flagship identity blocker was case-sensitive."""
    assert _blockers("He looked at Roster 4.") == _blockers("He looked at roster 4.")
