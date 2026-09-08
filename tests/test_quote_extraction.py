"""A quoted claim has to survive being quoted.

`_sentences` is the front of four features -- receipts, takes, history and
the team briefing -- and all four make the same promise: the Commissioner's
sentence, as he wrote it. Three things were breaking that promise in
published output, and all three were this function's job:

* Markdown reached the page as marks. 22 built files carried
  `**Swanson** - Competitive but Flawed`.
* Raw HTML in a section reached the page as escaped tags. 9 team pages
  carried `&lt;b&gt;Jordan Love, QB (EMCO)&lt;/b&gt;`.
* The splitter ended a sentence at every period, so a quote could stop at
  `LAC vs.` and be published as the whole claim.

The last one is the worst of the three: the other two look unfinished, but a
sentence cut at an abbreviation changes what the Commissioner said.
"""
from __future__ import annotations

from leaguepage.receipts import _sentences


def only(text: str) -> str:
    got = _sentences(text)
    assert len(got) == 1, f"expected one sentence, got {len(got)}: {got}"
    return got[0]


# --------------------------------------------------------------- markup

def test_bold_is_not_part_of_what_he_said():
    said = only("**Swanson** carries a lot of weight, and the backfield "
                "gives him a credible lead option here.")
    assert "**" not in said
    assert said.startswith("Swanson carries")


def test_raw_html_in_a_section_never_reaches_the_quote():
    said = only("<b>Jordan Love, QB (EMCO)</b> is the sort of bet that "
                "decides whether this roster has a ceiling worth the price.")
    assert "<" not in said and ">" not in said
    assert "Jordan Love" in said


def test_a_link_keeps_its_words_and_loses_its_target():
    said = only("The [Week 3 preview](../w3/index.html) called this room "
                "thin, and nothing since has argued otherwise at all.")
    assert "index.html" not in said and "[" not in said
    assert "Week 3 preview" in said


def test_a_snake_case_word_is_not_mistaken_for_emphasis():
    """`_{2,}` and not `_`: one underscore is usually a word, not a mark."""
    said = only("The story_candidate_id column carries the join, and the "
                "rest of the pipeline depends on it staying stable.")
    assert "story_candidate_id" in said


# ------------------------------------------------- abbreviation splitting

def test_a_matchup_abbreviation_does_not_end_the_sentence():
    said = only("Justin Herbert, QB - Gary - LAC vs. ARI is the risky "
                "opener, and the secondary he faces is the reason why.")
    assert said.endswith("reason why.")
    assert "vs. ARI" in said


def test_initials_stay_inside_the_name():
    said = only("A.J. Brown and Marvin Harrison Jr. keep the room strong, "
                "and the only reason this is not No. 1 is the thin bet.")
    assert "A.J. Brown" in said
    assert "Marvin Harrison Jr." in said
    assert "No. 1" in said


def test_a_decimal_is_not_a_full_stop():
    said = only("The 48.44 he put up in Week 9 is still the number this "
                "whole assumption is quietly resting on, for now.")
    assert "48.44" in said


def test_a_real_full_stop_still_ends_a_sentence():
    """The protection must not weld the whole paragraph into one quote."""
    got = _sentences(
        "The first assumption here is that the room carries itself. "
        "The second assumption is that the schedule stays soft for them.")
    assert len(got) == 2
    assert got[0].endswith("carries itself.")


# ------------------------------------------------------- existing contract

def test_headings_and_table_rows_are_still_not_prose():
    got = _sentences(
        "# The Lowdown\n\n"
        "| 0 | 3 | Statistical Anomalies | A- | 139. |\n\n"
        "The room is thin at running back, and the bet is that it holds.")
    assert len(got) == 1
    assert "Statistical Anomalies" not in got[0]


def test_a_fragment_is_still_too_short_to_quote():
    assert _sentences("Yes. No. Maybe.") == []
