"""Reading the Commissioner's ranking back out of the issue he published."""
from __future__ import annotations

from leaguepage.published_ranking import disagreements, extract_ranking

NAMES = {1: "The Dude Abides (The Dude)", 2: "George & Friends (DIP)",
         3: "Love Sutton Brocks (EMCO)", 4: "Tua Girls One Kupp (HOP)"}
TOKENS = {1: {"dude", "abides"}, 2: {"george", "friends"},
          3: {"love", "sutton", "brocks"}, 4: {"tua", "girls", "kupp"}}


def _section(md: str, title: str = "Draft Power Rankings") -> list[dict]:
    return [{"title": title, "anchor": "custom", "content_md": md}]


def _extract(md: str, names=None, tokens=None):
    return extract_ranking(_section(md), league_slug="disco", season="2026",
                           issue_key="draft", name_tokens=tokens or TOKENS,
                           public_names=names or NAMES)


def test_a_complete_numbered_ranking_is_read():
    md = ("### 1. The Dude Abides (The Dude)\n\nWords.\n\n"
          "### 2. George & Friends (DIP)\n\nWords.\n\n"
          "### 3. Love Sutton Brocks (EMCO)\n\nWords.\n\n"
          "### 4. Tua Girls One Kupp (HOP)\n\nWords.\n")
    out = _extract(md)
    assert out
    assert [r["rank"] for r in out["rows"]] == [1, 2, 3, 4]
    assert out["rows"][0]["roster_id"] == 1
    assert out["section_title"] == "Draft Power Rankings"


def test_a_top_three_list_is_not_a_ranking():
    """Below three quarters of the league it is an aside, not an order."""
    md = ("### 1. The Dude Abides (The Dude)\n\nWords.\n\n"
          "### 2. George & Friends (DIP)\n\nWords.\n")
    assert _extract(md) is None


def test_a_ranking_that_does_not_start_at_one_is_refused():
    md = ("### 2. The Dude Abides (The Dude)\n\nWords.\n\n"
          "### 3. George & Friends (DIP)\n\nWords.\n\n"
          "### 4. Love Sutton Brocks (EMCO)\n\nWords.\n\n"
          "### 5. Tua Girls One Kupp (HOP)\n\nWords.\n")
    assert _extract(md) is None


def test_unnumbered_headings_are_not_a_ranking():
    """Team capsules in issue order are not an opinion about order."""
    md = ("### The Dude Abides (The Dude)\n\nWords.\n\n"
          "### George & Friends (DIP)\n\nWords.\n\n"
          "### Love Sutton Brocks (EMCO)\n\nWords.\n\n"
          "### Tua Girls One Kupp (HOP)\n\nWords.\n")
    assert _extract(md) is None


def test_a_heading_that_merely_contains_a_number_is_not_a_rank():
    md = ("## Week 3 Notes\n\nWords.\n\n"
          "## Week 4 Notes\n\nWords.\n\n"
          "## Week 5 Notes\n\nWords.\n\n"
          "## Week 6 Notes\n\nWords.\n")
    assert _extract(md) is None


def test_a_duplicated_rank_makes_the_section_unreadable():
    """A half-read ranking is worse than none, so it is refused whole."""
    md = ("### 1. The Dude Abides (The Dude)\n\nWords.\n\n"
          "### 1. George & Friends (DIP)\n\nWords.\n\n"
          "### 2. Love Sutton Brocks (EMCO)\n\nWords.\n\n"
          "### 3. Tua Girls One Kupp (HOP)\n\nWords.\n")
    out = _extract(md)
    assert out is None or [r["rank"] for r in out["rows"]] != [1, 1, 2, 3]


def test_the_fullest_ranking_in_an_issue_wins():
    full = ("### 1. The Dude Abides (The Dude)\n\n"
            "### 2. George & Friends (DIP)\n\n"
            "### 3. Love Sutton Brocks (EMCO)\n\n"
            "### 4. Tua Girls One Kupp (HOP)\n")
    aside = ("### 1. Tua Girls One Kupp (HOP)\n\n"
             "### 2. Love Sutton Brocks (EMCO)\n\n"
             "### 3. George & Friends (DIP)\n")
    sections = [{"title": "Three to watch", "anchor": "a", "content_md": aside},
                {"title": "Draft Power Rankings", "anchor": "b", "content_md": full}]
    out = extract_ranking(sections, league_slug="disco", season="2026",
                          issue_key="draft", name_tokens=TOKENS, public_names=NAMES)
    assert out["section_title"] == "Draft Power Rankings"
    assert len(out["rows"]) == 4


# ------------------------------------------------------- disagreements

def test_disagreements_lead_with_the_widest_gap():
    rows = [
        {"name": "A", "rank": 1, "model_rank": 8, "model_gap": 7},
        {"name": "B", "rank": 2, "model_rank": 1, "model_gap": -1},
        {"name": "C", "rank": 7, "model_rank": 2, "model_gap": -5},
        {"name": "D", "rank": 4, "model_rank": 4, "model_gap": 0},
    ]
    out = disagreements(rows)
    assert [d["name"] for d in out] == ["A", "C", "B"]
    assert out[0]["gap"] == 7
    assert "less convinced" in out[0]["line"]
    assert "likes them more" in out[1]["line"]


def test_agreement_is_not_a_disagreement():
    rows = [{"name": "A", "rank": 1, "model_rank": 1, "model_gap": 0}]
    assert disagreements(rows) == []
