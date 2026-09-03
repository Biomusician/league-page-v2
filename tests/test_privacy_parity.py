"""Two gates that claimed to check the same thing and did not.

The publish gate scans issue prose before the snapshot is frozen. The build
audit scans the rendered site afterwards. Between them sits an irreversible
step, so a shape the second catches and the first does not is not a
duplicated check — it is a permanent mistake with a delay on it.
"""
from __future__ import annotations

import pytest

from leaguepage.privacy import (ALWAYS_FORBIDDEN, MIN_HANDLE_LEN, PRIVATE_PATTERNS,
                                handle_re, published_matcher)


# ------------------------------------------------------- the name matcher

@pytest.mark.parametrize("text,hit", [
    ("The Commissioner spoke to Fingers about it.", True),
    ("fingers crossed", True),                 # lowercase still identifies
    ("He had butterfingers all season.", False),   # not a word boundary
    ("Fingersmith", False),
    ("(Fingers)", True),
    ("Fingers, again.", True),
])
def test_a_name_is_matched_the_way_a_reader_reads_it(text, hit):
    """`h in text` was too loose and too tight at once: it fired inside
    longer words and missed a lowercased mention of the same person."""
    assert bool(handle_re("Fingers").search(text)) is hit


def test_the_matcher_survives_a_name_with_punctuation_in_it():
    assert handle_re("U.S. Trash").search("beat U.S. Trash by nine")
    assert handle_re("double-tds").search("the DOUBLE-TDS roster")


# ----------------------------------------------- the published subtraction

def test_a_nickname_a_manager_published_himself_is_not_private():
    """Half of every manager's aliases are in his own team name. Scanning
    without subtracting them flagged 103 violations on a clean build."""
    is_published = published_matcher(["Statistical Anomalies (McLovin)"])
    assert is_published("McLovin")
    assert is_published("Statistical Anomalies")   # a run of whole words
    assert is_published("statistical-anomalies")   # the slug of the same


def test_a_name_hidden_inside_another_word_is_still_private():
    """Testing containment against all the names joined together dropped any
    alias that happened to sit inside some other team's name — eleven
    candidates on live data, six of them first-name shaped, unscanned across
    the whole site."""
    is_published = published_matcher(["Corn Fed Fatties (Babe)",
                                      "Statistical Anomalies (McLovin)"])
    assert not is_published("Anne")     # inside "Anomalies", not a word of it
    assert not is_published("Corn Fed Fat")
    assert not is_published("Lovin")
    assert is_published("Babe")


def test_a_name_that_spans_two_teams_is_not_treated_as_published():
    """The old test joined the names, so a candidate straddling the join
    matched nothing real."""
    is_published = published_matcher(["Team One", "Two Team"])
    assert not is_published("OneTwo")


# --------------------------------------------------------- shared shapes

@pytest.mark.parametrize("label", [
    "Supabase project URL", "JWT", "Supabase key", "database URL",
    "GitHub token", "Anthropic key", "AWS access key", "Slack token",
    "Google API key", "private key", "email address",
])
def test_every_credential_shape_is_in_the_one_shared_list(label):
    """The site audit and the repo audit kept separate lists that disagreed:
    a database URL was blocked from dist/ but committable to main, and an AWS
    key was the other way round."""
    assert any(lab == label for _pat, lab in PRIVATE_PATTERNS), label


@pytest.mark.parametrize("text,label", [
    ("sb" + "_publishable_" + "a" * 24, "Supabase key"),
    ("sb" + "_secret_" + "b" * 24, "Supabase key"),
    ("AKIA" + "A" * 16, "AWS access key"),
    ("xox" + "b-" + "1" * 20, "Slack token"),
    ("AIza" + "x" * 35, "Google API key"),
    ("postgres" + "://u:p@h/db", "database URL"),
])
def test_shapes_that_used_to_pass_one_audit_and_fail_the_other(text, label):
    hits = [lab for pat, lab in PRIVATE_PATTERNS if pat.search(text)]
    assert label in hits, (text[:12], hits)


def test_the_repo_audit_reads_the_same_definitions():
    """Not a copy of them."""
    import ast
    import pathlib

    src = pathlib.Path("scripts/audit_repo_privacy.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
    assert "leaguepage.privacy" in imported
    # and does not redefine its own regex list beside it
    assert "TOKEN_PATTERNS = [\n    (re.compile" not in src


def test_the_forbidden_marker_list_still_covers_the_authoring_artifacts():
    for marker in ("AUTHORING", "commissioner_notes", "REVIEW_PACKET"):
        assert marker in ALWAYS_FORBIDDEN


def test_the_handle_floor_is_stated_once():
    assert MIN_HANDLE_LEN >= 3
