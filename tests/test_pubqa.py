"""Publication quality gate.

The acceptance scenario the tranche asked for lives in
`test_synthetic_bad_issue_*`: one issue carrying every class of real defect
plus one deliberate fragment/joke that must survive untouched. The rest of
the file pins individual detectors and, just as importantly, the
false-positive guards — a gate that cries wolf about voice gets turned off,
and then it protects nothing.
"""
from __future__ import annotations

import pytest

from leaguepage import pubqa
from leaguepage.pubqa import BLOCKER, WARNING, QAContext
from tests.fixtures import TEST_LEAGUE, populate_league


def ctx(**over) -> QAContext:
    base = dict(
        league_slug="testleague", season="2026", issue_key="draft",
        public_names={1: "Los Bandidos (Bandit)", 2: "Wild SeeKats (Seabass)",
                      3: "Statistical Anomalies (McLovin)", 4: "Dave",
                      5: "SHACtin' a Fool (SHAC)"},
        n_teams=5,
    )
    base.update(over)
    c = QAContext(**{k: v for k, v in base.items()
                     if k in QAContext.__dataclass_fields__})
    c.name_tokens = {rid: pubqa._norm_tokens(nm) for rid, nm in c.public_names.items()}
    return c


def check(text, *, module_key="lowdown", c=None, published=False):
    return pubqa.check_sections(
        [{"module_key": module_key, "title": "The Lowdown", "content_md": text}],
        c or ctx(), published=published)


def titles(findings):
    return [f.title for f in findings]


def only(findings, category):
    return [f for f in findings if f.category == category]


# ------------------------------------------------------- the whole scenario

BAD_ISSUE = """\
The Lowdown, week of the balloon going up.

Roster 4 came into this season with a plan and left with a rumor. Their
GM said the room was set, Our reading of the board says otherwise..
The waiver wire is the defense industrial base, and it is way to many
moves for one week.

### 2. team name pending

Preview pending.

RB room ranks 3 of 10, which is fine.

Woof.
"""


def test_synthetic_bad_issue_blocks_the_real_defects():
    fs = check(BAD_ISSUE)
    t = titles(fs)
    assert "Unresolved roster placeholder" in t
    assert "'team name pending' in published prose" in t
    assert "Raw placeholder in prose" in t
    assert all(f.severity == BLOCKER for f in fs
               if f.title in {"Unresolved roster placeholder",
                              "'team name pending' in published prose",
                              "Raw placeholder in prose"})


def test_synthetic_bad_issue_warns_on_copy_and_leaves_the_joke_alone():
    fs = check(BAD_ISSUE)
    t = titles(fs)
    assert "Doubled punctuation" in t
    assert "Two sentences joined by a comma" in t
    assert "'to' where 'too' belongs" in t
    for f in fs:
        if f.severity == WARNING and f.category == pubqa.COPY:
            assert "Woof." not in (f.excerpt or "")
    # a one-word sentence fragment is voice, not a defect
    assert not [f for f in check("Woof.") if f.category == pubqa.COPY]


def test_synthetic_bad_issue_report_is_not_ready():
    rep = pubqa.report(check(BAD_ISSUE))
    assert rep["ready"] is False
    assert rep["headline"].startswith("NOT READY")
    assert len(rep["blockers"]) >= 3


def test_stale_rank_claim_is_a_warning_with_before_and_after():
    c = ctx(positional_ranks={1: {"RB": 9}}, positions_n=5)
    fs = check("## Los Bandidos (Bandit)\n\nRB room ranks 3 of 10.\n",
               module_key="custom", c=c)
    stale = [f for f in fs if f.title == "Rank claim no longer matches the data"]
    assert len(stale) == 1
    assert stale[0].severity == WARNING
    assert any("RB #3" in e for e in stale[0].evidence)
    assert any("RB #9" in e for e in stale[0].evidence)


# ------------------------------------------------------------ voice guards
#
# Each of these is something the Commissioner actually writes. None of them
# may produce a finding.

VOICE_SAMPLES = [
    "Woof.",
    "Respect for the conviction. Woof for the math.",
    "the draft rank says the bit coulda been sharper",
    "I looked at Jacksonville's offense and said \"more, please, four times.\"",
    "Divest-to-invest is real doctrine. This is divest-and-hope.",
    "Nineteen picks, plus-179. 'Nuff said.",
    "Two quarterbacks in superflex is where he's gonna run into issues.",
    "when an ACS (especially if it used to be an OSS) first stands up, it's chaos",
    "Godspeed everyone! Matchup write-ups next week!",
    "Is my methodology flawed? Who knows.",
    "Eighth is generous and I will not be appealing.",
    "we'll know who spent the draft deccing chaff",
    "Wait... what?",
]


@pytest.mark.parametrize("sample", VOICE_SAMPLES)
def test_voice_is_never_flagged(sample):
    assert check(sample) == [], f"flagged: {titles(check(sample))}"


def test_proper_noun_after_a_comma_is_not_a_comma_splice():
    # "The Dude" is a team, not the start of a new sentence
    assert not only(check("I look forward to it, The Dude Abides printing this out."),
                    pubqa.COPY)


def test_ellipsis_and_emphatic_punctuation_survive():
    assert not only(check("Well... that happened. Really?! Sure!!"), pubqa.COPY)


# ------------------------------------------------------------- identity

def test_heading_shorthand_for_a_team_is_accepted():
    text = "\n".join(f"### {i}. {nm}\n\nprose\n" for i, nm in enumerate(
        ["Los Bandidos", "Wild SeeKats", "Statistical Anomalies", "Dave",
         "SHACtin a fool"], start=1))
    assert not only(check(text, module_key="custom"), pubqa.IDENTITY)


def test_heading_with_a_foreign_token_is_flagged_with_the_current_name():
    text = "\n".join(f"### {i}. {nm}\n\nprose\n" for i, nm in enumerate(
        ["Los Bandidos", "Wild SeeKats", "Statistical Anomalies (Ethen)", "Dave",
         "SHACtin a fool"], start=1))
    fs = only(check(text, module_key="custom"), pubqa.IDENTITY)
    assert len(fs) == 1
    assert fs[0].severity == WARNING
    assert "ethen" in fs[0].detail
    assert "Statistical Anomalies (McLovin)" in fs[0].evidence[0]


def test_heading_naming_nobody_is_flagged():
    text = "\n".join(f"### {i}. {nm}\n\nprose\n" for i, nm in enumerate(
        ["Los Bandidos", "Wild SeeKats", "Jesse", "Dave", "SHACtin a fool"],
        start=1))
    fs = only(check(text, module_key="custom"), pubqa.IDENTITY)
    assert [f.title for f in fs] == ["Heading names no known team"]


def test_structural_heading_at_the_team_level_is_not_a_team():
    text = "\n".join(f"### {i}. {nm}\n\nprose\n" for i, nm in enumerate(
        ["Los Bandidos", "Wild SeeKats", "Statistical Anomalies", "Dave",
         "SHACtin a fool"], start=1)) + "\n### Second Opinions\n\nprose\n"
    assert not only(check(text, module_key="custom"), pubqa.IDENTITY)


def test_internal_slugs_block():
    fs = check("See roster-4 and los-bandidos-vs-wild-seekats for detail.")
    assert len(only(fs, pubqa.IDENTITY)) == 2
    assert all(f.severity == BLOCKER for f in only(fs, pubqa.IDENTITY))


def test_private_handle_blocks_and_cannot_be_ignored():
    c = ctx(private_handles=["confedfatties"])
    fs = check("confedfatties had a night.", c=c)
    priv = only(fs, pubqa.PRIVACY)
    assert priv and priv[0].privacy is True and priv[0].severity == BLOCKER
    assert "confedfatties" not in (priv[0].excerpt or "")
    # ignoring is only ever honored for warnings
    rep = pubqa.report(fs, ignored={priv[0].finding_id})
    assert rep["ready"] is False and rep["has_privacy_blocker"] is True


# ------------------------------------------------------------ formatting

def test_markdown_that_failed_to_render_blocks():
    fs = check("Some prose.\n\n    ### 2. Los Bandidos\n\nMore prose.\n")
    assert "Markdown heading did not render" in titles(fs)
    assert only(fs, pubqa.FORMATTING)[0].severity == BLOCKER


def test_relative_link_blocks_but_absolute_and_anchor_do_not():
    assert "Link target is not publishable" in titles(check("See [the board](../draft/)."))
    assert not only(check("See [the board](https://example.test/draft/)."), pubqa.FORMATTING)
    assert not only(check("See [above](#lowdown)."), pubqa.FORMATTING)


def test_empty_included_section_blocks():
    fs = pubqa.check_sections(
        [{"module_key": "blackbox", "title": "Black Box", "content_md": "  "}], ctx())
    assert fs[0].severity == BLOCKER
    assert fs[0].title == "Included section is empty"


def test_duplicate_heading_is_only_a_warning():
    fs = check("## Reading the Moves\n\na\n\n## Reading the Moves\n\nb\n")
    dup = [f for f in fs if f.title == "Duplicate heading"]
    assert dup and dup[0].severity == WARNING


# ------------------------------------- analytical consistency (K/DST rule)

def _ranking_section(extra: str = "") -> str:
    body = "\n".join(
        f"### {i}. {nm} (-{i * 40})\n\nSome analysis here.\n"
        for i, nm in enumerate(["Los Bandidos", "Wild SeeKats",
                                "Statistical Anomalies", "Dave",
                                "SHACtin a fool"], start=1))
    return body + extra


def test_ranking_driven_by_kicker_deltas_is_flagged():
    fs = check(_ranking_section(
        "\nThe Fairbairn kicker premium (minus-80) is the stain here.\n"
        "Paying early at kicker (minus-73) is the rest of it.\n"),
        module_key="custom")
    an = only(fs, pubqa.ANALYTICS)
    assert an and an[0].severity == WARNING
    assert "headline_deviations" in " ".join(an[0].evidence)


def test_the_same_ranking_with_the_disclosure_is_not_flagged():
    fs = check(_ranking_section(
        "\nThe Fairbairn kicker premium (minus-80) is the stain here.\n"
        "Paying early at kicker (minus-73) is the rest of it. Consensus ranks "
        "special teams below the draftable range, so read those two lines as a "
        "reference-board artifact rather than a roster decision.\n"),
        module_key="custom")
    assert not only(fs, pubqa.ANALYTICS)


def test_a_ranking_with_no_special_teams_content_is_not_flagged():
    fs = check(_ranking_section("\nQuarterback timing decided this board.\n"),
               module_key="custom")
    assert not only(fs, pubqa.ANALYTICS)


# ------------------------------------------------ ignore store / reporting

def test_ignoring_a_warning_clears_it_but_not_a_blocker(storage):
    fs = check(BAD_ISSUE)
    warn = next(f for f in fs if f.severity == WARNING)
    block = next(f for f in fs if f.severity == BLOCKER)
    pubqa.ignore_finding(storage, "testleague", "2026", "draft", warn.finding_id)
    pubqa.ignore_finding(storage, "testleague", "2026", "draft", block.finding_id)
    ign = pubqa.ignored_findings(storage, "testleague", "2026", "draft")
    rep = pubqa.report(fs, ignored=ign)
    assert warn.finding_id not in [f["finding_id"] for f in rep["warnings"]]
    assert block.finding_id in [f["finding_id"] for f in rep["blockers"]]

    pubqa.unignore_finding(storage, "testleague", "2026", "draft", warn.finding_id)
    assert warn.finding_id not in pubqa.ignored_findings(
        storage, "testleague", "2026", "draft")


def test_clean_issue_reports_ready():
    rep = pubqa.report(check("A clean paragraph with nothing wrong in it.\n"))
    assert rep["ready"] is True
    assert rep["headline"] == "READY · 0 blockers · 0 warnings"


def test_accept_suggestion_is_a_literal_replacement():
    text = "Their GM said the room was set, Our reading says otherwise.\n"
    f = next(f for f in check(text) if f.title == "Two sentences joined by a comma")
    assert f.fix_from and f.fix_to
    assert text.count(f.fix_from) == 1
    assert ". Our reading" in text.replace(f.fix_from, f.fix_to)


# ------------------------------------------------------------ live context

def test_build_context_reads_current_names_and_rosters(storage):
    populate_league(storage, TEST_LEAGUE, teams=4, rounds=2)
    c = pubqa.build_context(storage, TEST_LEAGUE, "2026", "draft")
    assert c.n_teams == 4
    assert c.public_names[1] == "Team 1"
    assert c.rosters[1]["drafted"]        # draft picks landed


def test_only_mechanical_copy_findings_offer_an_automatic_fix():
    """scripts/apply_qa_fixes.py applies exactly the findings that carry a
    fix pair. Nothing outside COPY may ever carry one: what a team is called
    and what a number means are the Commissioner's calls, not a script's."""
    c = ctx(private_handles=["confedfatties"], positional_ranks={1: {"RB": 9}},
            positions_n=5)
    text = "\n".join(f"### {i}. {nm}\n\nRB room ranks 3 of 10.\n"
                     for i, nm in enumerate(
                         ["Los Bandidos", "Wild SeeKats", "Jesse", "Dave",
                          "SHACtin a fool"], start=1))
    text += ("\n\nRoster 4 said it there.. Their GM was sure, Our reading "
             "differs. Preview pending. confedfatties agreed.\n")
    for f in check(text, module_key="custom", c=c):
        if f.fix_from or f.fix_to:
            assert f.category == pubqa.COPY, f"{f.category} offered an auto-fix"
            assert f.fix_from and f.fix_to
            assert f.fix_from != f.fix_to


def test_a_table_column_headed_hash_is_not_a_broken_heading():
    r"""Caught publishing a real correction: the leaked-heading pattern used
    \s, which spans newlines, so any table with a "#" column header — every
    standings and ranking table — was reported as broken markup."""
    table = ("Recomputed, the order changes:\n\n"
             "| # | Team | Value |\n| --- | --- | --- |\n"
             "| 1 | Los Bandidos (Bandit) | +118 |\n"
             "| 2 | Wild SeeKats (Seabass) | -13 |\n")
    assert not only(check(table, module_key="custom"), pubqa.FORMATTING)


def test_a_genuinely_unrendered_heading_is_still_caught():
    # four-space indent makes python-markdown treat it as a code block
    fs = check("Some prose.\n\n    ## Correction — methodology\n\nMore prose.\n")
    assert "Markdown heading did not render" in titles(fs)
