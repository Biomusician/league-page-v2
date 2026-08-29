from __future__ import annotations

from leaguepage.style_check import check_text


def kinds(text):
    return [w["kind"] for w in check_text(text)]


def test_em_dash_and_en_dash_caught():
    assert "em-dash" in kinds("His team — a disaster — lost again.")
    assert "em-dash" in kinds("The score was 101–95 in the end.")


def test_negated_parallel_family():
    assert "negated-parallel" in kinds("This is not a rebuild, but a collapse.")
    assert "negated-parallel" in kinds("It isn't a slump, it's a lifestyle.")
    assert "negated-parallel" in kinds("It's not about the points. It's about the shame.")
    assert "negated-parallel" in kinds("He didn't just lose; his team was not merely bad but historically so.")
    # sentence whose punch is a bare terminal negation of the parallel
    assert "negated-parallel" in kinds("The ritual is worth keeping. The full day is not.")


def test_banned_phrases_and_tells():
    assert "banned-phrase" in kinds("This should be an exciting matchup for both sides.")
    assert "banned-phrase" in kinds("Only time will tell if the gamble pays off.")
    assert "banned-phrase" in kinds("Both teams will be looking to establish the run.")
    assert "banned-phrase" in kinds("We must delve into the roster construction.")
    assert "writerly-tell" in kinds("He quietly became the league's best manager.")


def test_clean_jonathan_style_prose_passes():
    clean = (
        "Three things decided this one. CMC did CMC things, the Bills stack "
        "cashed in, and the bench outscored two starters. He's the Breitling, "
        "and everyone else is a Casio. Welp. Best of luck to everyone except "
        "my opponent this week.\n"
    )
    assert check_text(clean) == []


def test_headings_and_comments_skipped():
    text = "# Header — with dash\n<!-- comment — dash -->\nClean prose line.\n"
    warnings = check_text(text)
    assert all(w["kind"] != "em-dash" or w["line"] == 1 for w in warnings)
