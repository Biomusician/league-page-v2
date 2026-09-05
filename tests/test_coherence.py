"""Cross-section coherence: the checks that ask whether the issue was
assembled by one editor.

Each detector gets a true positive and the false positive it must not
raise. Warnings only, all of them: the Commissioner reads and rules.
"""
from __future__ import annotations

from leaguepage import coherence
from leaguepage.coherence import (callsigns_from_names, check, normalize_paragraph,
                                  other_league_paragraphs)

PUBLIC = {1: "Statistical Anomalies (McLovin)", 5: "Love Sutton Brocks (EMCO)",
          4: "Stafford and Sons (Fingers)", 12: "Secret Asian Man (POP)",
          7: "Wild SeeKats (Seebass/Kats)"}
SLEEPER = {1: "Statistical Anomalies", 5: "Love Sutton Brocks", 4: "Stafford&Son",
           12: "Secret Asian Man", 7: "Wild SeeKats"}
PLAYERS = {
    "Josh Jacobs": {"rid": 5, "position": "RB", "status": "NA"},
    "Kyle Pitts": {"rid": 12, "position": "TE", "status": None},
    "Jeremiyah Love": {"rid": 12, "position": "RB", "status": "Questionable"},
    "Saquon Barkley": {"rid": 5, "position": "RB", "status": None},
}


def cctx(**over):
    base = {"format": "superflex", "public_names": PUBLIC, "sleeper_names": SLEEPER,
            "players": PLAYERS, "callsigns": callsigns_from_names(PUBLIC),
            "former_names": {"George & Friends": 4}}
    base.update(over)
    return base


def sec(key, text):
    return {"module_key": key, "title": key, "content_md": text}


def titles(findings):
    return [f["title"] for f in findings]


# ---------------------------------------------------------------- format

def test_1qb_advice_in_a_superflex_league_is_flagged():
    fs = coherence.format_mismatch([sec("fades", "Sit in normal 1QB formats if you can.")],
                                   cctx(format="superflex"))
    assert titles(fs) == ["Copy written for the other lineup format"]
    assert "Superflex" in fs[0]["detail"]


def test_superflex_consensus_in_a_1qb_league_is_flagged():
    fs = coherence.format_mismatch([sec("tracks", "FantasyPros' Week 1 Superflex consensus has him QB5.")],
                                   cctx(format="1qb"))
    assert len(fs) == 1


def test_the_right_format_is_never_flagged():
    assert not coherence.format_mismatch([sec("tracks", "Still usable in Superflex.")],
                                         cctx(format="superflex"))
    assert not coherence.format_mismatch([sec("tracks", "A fine 1QB start.")], cctx(format="1qb"))
    assert not coherence.format_mismatch([sec("tracks", "Sit him in 1QB.")], cctx(format=None))


# -------------------------------------------------------------- identity

def test_the_raw_sleeper_name_is_flagged_when_the_public_name_differs():
    fs = coherence.stale_team_names([sec("hardware", "-Stafford&Son — 27.3")], cctx())
    assert titles(fs) == ["Team named by a name the paper does not use"]
    assert fs[0]["suggestion"] == "Stafford and Sons (Fingers)"


def test_a_former_name_is_flagged():
    fs = coherence.stale_team_names([sec("hardware", "-George & Friends — 25.6")], cctx())
    assert len(fs) == 1 and "former name" in fs[0]["detail"]


def test_a_sleeper_name_that_matches_the_public_name_is_fine():
    fs = coherence.stale_team_names([sec("power", "Secret Asian Man leads.")], cctx())
    assert not fs


def test_a_player_attributed_to_the_wrong_owner_is_flagged():
    fs = coherence.owner_attribution([sec("fades", "Kyle Pitts, TE (EMCO) — Atlanta at Pittsburgh")],
                                     cctx())
    assert titles(fs) == ["Player attributed to the wrong roster"]
    assert "Secret Asian Man" in fs[0]["detail"]


def test_the_right_owner_by_callsign_or_team_name_is_fine():
    fs = coherence.owner_attribution(
        [sec("fades", "Kyle Pitts, TE (POP) — Atlanta at Pittsburgh"),
         sec("tracks", "Kyle Pitts, TE — Secret Asian Man (POP) — ATL at PIT")], cctx())
    assert not fs


def test_a_two_manager_callsign_resolves():
    players = {"Jayden Daniels": {"rid": 7, "position": "QB", "status": None}}
    fs = coherence.owner_attribution([sec("tracks", "Jayden Daniels, QB — Wild SeeKats (Seebass/Kats) — WAS at PHI")],
                                     cctx(players=players))
    assert not fs


# ------------------------------------------------------------- freshness

def test_an_na_player_praised_as_a_strength_is_flagged():
    fs = coherence.unavailable_players_cited(
        [sec("ctp", "Six running backs, led by Saquon Barkley and Josh Jacobs, good for the 2nd-ranked RB room.")],
        cctx())
    assert titles(fs) == ["Player with an out-type designation is written up as available"]
    assert "'NA'" in fs[0]["detail"]


def test_a_paragraph_that_knows_he_is_hurt_is_not_flagged():
    fs = coherence.unavailable_players_cited(
        [sec("lowdown", "The one with Josh Jacobs was born with birth defects, and Jacobs is out anyway.")],
        cctx())
    assert not fs


def test_questionable_is_not_an_out_designation():
    fs = coherence.unavailable_players_cited([sec("tracks", "Jeremiyah Love gets the start.")], cctx())
    assert not fs


# ------------------------------------------------------------- coherence

def test_two_sections_disagreeing_on_a_rank_are_flagged():
    claims = [{"module_key": "ctp", "rid": 5, "pos": "RB", "claimed": 2},
              {"module_key": "power", "rid": 5, "pos": "RB", "claimed": 4}]
    fs = coherence.rank_claim_conflicts(claims, cctx())
    assert titles(fs) == ["Sections disagree about a positional rank"]
    assert "#2 in ctp" in fs[0]["detail"] and "#4 in power" in fs[0]["detail"]


def test_agreeing_sections_are_fine():
    claims = [{"module_key": "ctp", "rid": 5, "pos": "RB", "claimed": 2},
              {"module_key": "power", "rid": 5, "pos": "RB", "claimed": 2}]
    assert not coherence.rank_claim_conflicts(claims, cctx())


def test_a_player_in_three_sections_is_flagged_and_in_two_is_not():
    three = [sec("lowdown", "Kyle Pitts again."), sec("ctp", "Kyle Pitts starts."),
             sec("tracks", "Kyle Pitts, TE (POP)")]
    fs = coherence.subject_saturation(three, cctx())
    assert titles(fs) == ["One player carries several sections"]
    assert "ctp, lowdown, tracks" in fs[0]["detail"]
    assert not coherence.subject_saturation(three[:2], cctx())


def test_a_single_word_name_never_saturates():
    players = {"Love": {"rid": 5, "position": "QB", "status": None}}
    fs = coherence.subject_saturation(
        [sec("a", "love"), sec("b", "Love"), sec("c", "LOVE")], cctx(players=players))
    assert not fs


PARA = ("Pitts finally delivered the season people had been drafting for years, "
        "finishing 2025 as the PPR TE2 with career highs in catches and touchdowns, "
        "and Pittsburgh allowed the second-most fantasy points to tight ends.")


def test_a_paragraph_copied_from_the_other_league_is_flagged():
    other = other_league_paragraphs([f"<b>Kyle Pitts</b>\n\n{PARA}\n\nThe play: TE1."])
    fs = coherence.cross_league_duplicates(
        [sec("tracks", f"Header\n\n{PARA.upper()}")],
        cctx(other_league_paragraphs=other, other_league_label="Disco Chat week-01"))
    assert titles(fs) == ["Paragraph also appears in the other league's issue"]
    assert "Disco Chat week-01" in fs[0]["detail"]


def test_short_and_different_paragraphs_are_not_duplicates():
    other = other_league_paragraphs(["The play: TE1.\n\n" + PARA])
    fs = coherence.cross_league_duplicates(
        [sec("tracks", "The play: TE1.\n\nA different paragraph about a different player entirely, "
                       "long enough to count as prose and not a heading, written for these readers.")],
        cctx(other_league_paragraphs=other))
    assert not fs


def test_normalisation_ignores_markup_and_case():
    assert normalize_paragraph("<b>Kyle</b> **Pitts**, TE.") == normalize_paragraph("kyle pitts te")


# ------------------------------------------------------------------ check

def test_check_runs_every_detector_and_returns_only_warnings():
    fs = check([sec("fades", "Sit in 1QB formats. Kyle Pitts, TE (EMCO) plays."),
                sec("hardware", "-Stafford&Son — 27.3")], cctx())
    assert {f["severity"] for f in fs} == {"warning"}
    assert len(fs) >= 3


def test_a_team_opening_the_next_sentence_is_not_an_attribution():
    """"my reward was Juwan Johnson. Swifty Mahomey at least got paid" names
    two subjects in two sentences; the second is not the first's owner."""
    cctx = {"players": {"Juwan Johnson": {"rid": 3, "position": "TE", "status": ""}},
            "callsigns": {"Pappie": 5, "McLovin": 3},
            "public_names": {3: "Statistical Anomalies (McLovin)", 5: "Swifty Mahomey (Pappie)"}}
    sections = [{"module_key": "lowdown", "content_md":
                 "my reward was Juwan Johnson. Swifty Mahomey at least got paid for waiting."}]
    assert coherence.owner_attribution(sections, cctx) == []
    wrong = [{"module_key": "tracks", "content_md": "Juwan Johnson, TE (Pappie) is a start."}]
    assert len(coherence.owner_attribution(wrong, cctx)) == 1
