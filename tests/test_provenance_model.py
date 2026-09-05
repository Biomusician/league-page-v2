"""The provenance model: origin, edited, assistance, and the edit metric.

Six public labels and nothing else. Origin is a recorded fact; editing
changes the label, never the origin; an exact reset restores the exact
state; the edit metric is a description for the Desk and decides nothing.
"""
from __future__ import annotations

import pytest

from leaguepage import provenance as pv

BASE = ("The room was set on Sunday, and then it was not. Four teams chased the "
        "same running back for a week, two of them paid, and the one that paid "
        "least started him. A first draft of history, written on Tuesday.")


def _row(origin=None, generator="claude-code", method="section-brief", text=BASE,
         assistance=None):
    return {"origin": origin, "generator": generator, "method": method,
            "generated_sha": pv.text_sha(text) if origin != "commissioner" else "",
            "baseline_text": pv.normalise(text) if origin != "commissioner" else None,
            "assistance": assistance}


# ------------------------------------------------------------ the six labels

def test_every_state_has_exactly_one_label():
    cases = [
        (_row("ai"), BASE, "AI-generated", "AI"),
        (_row("ai"), BASE + " One more sentence.", "AI-generated · Commish edited", "AI"),
        (_row("deterministic", generator="deterministic", method="weekly-awards"), BASE,
         "Automatically generated", "AUTO"),
        (_row("deterministic", generator="deterministic", method="weekly-awards"), "Rewritten.",
         "Automatically generated · Commish edited", "AUTO"),
        (_row("commissioner", generator=None), "Mine.", "Commish-written", "COMMISH"),
        (_row("commissioner", generator=None, assistance="ai-writing"), "Mine.",
         "Commish-written · AI-assisted", "COMMISH"),
        (_row("commissioner", generator=None, assistance="ai-research"), "Mine.",
         "Commish-written · AI-assisted", "COMMISH"),
    ]
    for row, text, label, badge in cases:
        st = pv.classify(row, text)
        assert st["label"] == label and st["badge_text"] == badge, (label, st)
        assert st["label"] in pv.LABELS


def test_unknown_origin_gets_no_label_at_all():
    assert pv.classify(None, BASE) is None
    assert pv.classify(_row("unknown", generator=None, assistance="ai-writing"), BASE) is None
    assert pv.classify({"origin": None, "generator": None, "generated_sha": ""}, BASE) is None


def test_rows_from_before_origin_existed_still_read():
    """The first provenance rows carried a generator and a hash only."""
    old_ai = {"generator": "claude-code", "method": "section-brief",
              "generated_sha": pv.text_sha(BASE)}
    old_auto = {"generator": "deterministic", "method": "transactions",
                "generated_sha": pv.text_sha(BASE)}
    assert pv.classify(old_ai, BASE)["label"] == "AI-generated"
    assert pv.classify(old_ai, BASE + " x")["label"] == "AI-generated · Commish edited"
    assert pv.classify(old_auto, BASE)["label"] == "Automatically generated"


def test_deterministic_assistance_is_not_ai_assistance():
    """Sleeper arithmetic and reference ranks are not an AI."""
    st = pv.classify(_row("commissioner", generator=None,
                          assistance="deterministic-analysis"), "Mine.")
    assert st["label"] == "Commish-written"


def test_ai_origin_ignores_the_assistance_axis_in_its_label():
    st = pv.classify(_row("ai", assistance="ai-writing"), BASE)
    assert st["label"] == "AI-generated"


def test_provider_and_method_unknown_are_said_not_guessed():
    st = pv.classify(_row("ai", generator="something-else", method="nope"), BASE)
    assert st["generator"] is None and st["method"] is None
    assert "provider not recorded" in st["detail"]
    assert "Generation method not recorded." in st["detail"]
    edited = pv.classify(_row("ai", generator=None, method=None), BASE + " x")
    assert edited["label"] == "AI-generated · Commish edited"
    assert "provider not recorded" in edited["detail"]


def test_the_public_dict_never_carries_the_baseline_or_a_number():
    st = pv.classify(_row("ai"), BASE + " x")
    for key in ("baseline_text", "generated_sha", "changed", "percent"):
        assert key not in st
    assert not any(ch.isdigit() for ch in st["label"])


def test_nothing_private_reaches_a_caption():
    bad = _row("ai", generator="C:/Users/Jonathan/secret", method="editorial/AUTHORING.md")
    st = pv.classify(bad, BASE)
    for leak in ("C:/", "Jonathan", "editorial/", "AUTHORING", ".md"):
        assert leak not in st["caption"] and leak not in st["detail"], leak
    st = pv.describe_commissioner(assisted=True, method="editorial/prompt.txt")
    assert "prompt" not in st["detail"]


def test_details_come_from_the_fixed_vocabulary():
    matchup = pv.describe_commissioner(assisted=True, method="matchup-brief")
    assert matchup["detail"] == ("Commissioner-written using AI-assisted matchup research "
                                 "and synced league data.")
    plain = pv.describe_commissioner()
    assert plain["detail"] == "Written by the Commissioner."
    auto = pv.describe_machine("transactions")
    assert auto["detail"].startswith("Produced from synced Sleeper league data")


def test_the_section_parent_of_the_previews_stays_silent(tmp_path):
    from leaguepage.storage import Storage

    with Storage(tmp_path / "t.sqlite3") as s:
        pv.record(s, league_slug="x", season="2027", issue_key="week-01", section="ctp",
                  generator="claude-code", method="matchup-brief", text=BASE)
        assert pv.section_state(s, league_slug="x", season="2027", issue_key="week-01",
                                section="ctp", text=BASE) is None
        assert pv.state_for(s, league_slug="x", season="2027", issue_key="week-01",
                            section="ctp", text=BASE) is not None


def test_old_snapshot_shapes_render_in_todays_shape():
    assert pv.public_shape(None) is None
    old = {"badge_text": "AI", "caption": "AI-generated by Claude Code from x. No Commissioner edits."}
    new = pv.public_shape(old)
    assert new["label"] == "AI-generated" and new["detail"] == old["caption"]
    auto = pv.public_shape({"badge_text": "AUTO", "caption": "Generated automatically."})
    assert auto["label"] == "Automatically generated"
    today = pv.describe(None, None)
    assert pv.public_shape(today) is today


def test_inline_markup_matches_the_template_classes():
    html = pv.inline_html(pv.describe("claude-code", "matchup-brief"))
    assert html.startswith('<p class="prov" role="note">')
    assert 'class="prov-mark" aria-hidden="true">AI<' in html
    assert "AI-generated" in html and "<h" not in html
    assert pv.inline_html(None) == ""


# ------------------------------------------------------------ recording

def test_assistance_survives_a_later_origin_act(tmp_path):
    from leaguepage.storage import Storage

    with Storage(tmp_path / "t.sqlite3") as s:
        kw = dict(league_slug="x", season="2027", issue_key="week-01", section="tracks")
        pv.note_assistance(s, kind="ai-writing", **kw)
        assert pv.state_for(s, text="anything", **kw) is None, "assistance alone labels nothing"
        pv.mark_commissioner(s, event="commissioner-save", **kw)
        assert pv.state_for(s, text="mine", **kw)["label"] == "Commish-written · AI-assisted"
        pv.record(s, generator="claude-code", method="section-brief", text=BASE,
                  event="proposal-accept", **kw)
        row = s.get_prose_provenance("x", "2027", "week-01", "tracks")
        assert row["assistance"] == "ai-writing" and row["origin"] == "ai"
        assert row["event"] == "proposal-accept"


def test_record_rejects_an_origin_outside_the_vocabulary(tmp_path):
    from leaguepage.storage import Storage

    with Storage(tmp_path / "t.sqlite3") as s:
        with pytest.raises(ValueError):
            pv.record(s, league_slug="x", season="2027", issue_key="week-01",
                      section="tracks", generator="claude-code", method=None,
                      text=BASE, origin="whoever")
        with pytest.raises(ValueError):
            pv.note_assistance(s, league_slug="x", season="2027", issue_key="week-01",
                               section="tracks", kind="magic")


def test_the_marker_and_comments_are_scaffolding_not_prose():
    marked = "<!-- ROUGH DRAFT - COMMISSIONER EDIT REQUIRED -->\n" + BASE + "\n<!-- usage: x -->\n"
    assert pv.text_sha(marked) == pv.text_sha(BASE)
    assert pv.text_sha(BASE.replace("\n", "\r\n") + "\n\n") == pv.text_sha(BASE)


# ------------------------------------------------------------ the edit metric

def pct(a, b):
    return pv.changed_from_baseline(a, b)


def test_metric_is_zero_only_for_equivalent_text():
    assert pct(BASE, BASE) == 0
    assert pct(BASE, BASE.replace("\n", "\r\n")) == 0
    assert pct(BASE, "  " + BASE + "\n\n") == 0
    assert pct(BASE, BASE + "\n<!-- a private note -->") == 0
    assert pct(BASE, BASE.replace("running back", "**running back**")) == 0, "formatting only"


def test_metric_red_team():
    words = BASE.split()
    one_comma = BASE.replace("Sunday, and", "Sunday and")
    typo = BASE.replace("Tuesday", "Tuesdya")
    ten_added = BASE + " " + " ".join(["extra"] * 10)
    same_length_rewrite = " ".join(["different"] * len(words))
    shorter_rewrite = " ".join(["different"] * (len(words) // 2))
    heading_change = "## Old heading\n\n" + BASE
    heading_changed = "## New heading\n\n" + BASE

    assert 1 <= pct(BASE, one_comma) <= 3, "punctuation counts modestly, not nothing"
    assert 1 <= pct(BASE, typo) <= 5
    assert 1 <= pct(BASE, ten_added) <= 25
    assert pct(BASE, same_length_rewrite) >= 95
    assert pct(BASE, shorter_rewrite) >= 95
    assert pct(BASE, "") == 100
    assert 1 <= pct(heading_change, heading_changed) <= 10, "a heading word is one word"
    assert pct(BASE, BASE.replace(". ", ".\n\n")) == 0, "paragraphing is not rewriting"
    assert pct(BASE, "> " + BASE) == 0, "a Markdown marker is not a word"


def test_metric_moves_and_deletions_feel_proportionate():
    p1 = "First paragraph with seven words in it."
    p2 = "Second paragraph with seven words in it."
    p3 = "Third paragraph with seven words in it."
    original = f"{p1}\n\n{p2}\n\n{p3}"
    moved = f"{p2}\n\n{p3}\n\n{p1}"
    deleted = f"{p1}\n\n{p3}"
    assert 25 <= pct(original, moved) <= 45, "a moved paragraph counts once, at its size"
    assert 25 <= pct(original, deleted) <= 40


def test_metric_is_bounded_deterministic_and_restorable():
    for a, b in ((BASE, "x" * 5000), ("", BASE), (BASE, BASE * 4)):
        v = pct(a, b)
        assert 0 <= v <= 100 and v == pct(a, b)
    edited = BASE.replace("Tuesday", "Wednesday")
    assert pct(BASE, edited) > 0
    assert pct(BASE, BASE) == 0, "restoring the exact text is 0 again"


def test_hints_are_a_reading_of_the_number_and_change_no_label():
    assert pv.changed_hint(0) == "exact generated baseline"
    assert pv.changed_hint(1) == "lightly edited" and pv.changed_hint(19) == "lightly edited"
    assert pv.changed_hint(20) == "edited" and pv.changed_hint(59) == "edited"
    assert pv.changed_hint(60) == "substantially rewritten" and pv.changed_hint(100) == "substantially rewritten"
    heavy = pv.classify(_row("ai"), " ".join(["new"] * 400))
    assert heavy["label"] == "AI-generated · Commish edited", "90% rewritten is still AI in origin"


def test_desk_line_says_the_metric_and_never_who_wrote_which_word():
    row = _row("ai")
    assert pv.desk_line(row, BASE) == "AI-origin draft · exact generated baseline"
    line = pv.desk_line(row, BASE + " And one more thing to say.")
    assert line.startswith("AI-origin draft · ~") and "changed from generated baseline" in line
    assert "human-written" not in line
    assert pv.desk_line(_row("commissioner", generator=None), "x") == "Commish-written"
    assert pv.desk_line(None, "x") == "Origin not recorded"
    assert pv.desk_line(_row("unknown", generator=None, assistance="ai-writing"), "x") \
        == "Origin not recorded (AI assistance noted)"
    no_base = dict(_row("ai")); no_base["baseline_text"] = None
    assert "no baseline kept" in pv.desk_line(no_base, BASE + " x")


def test_the_public_line_cannot_widen_a_phone_screen():
    """The markup has no fixed width; the stylesheet lets the label wrap
    under the mark and puts the detail on its own line."""
    import pathlib

    css = pathlib.Path("templates/public/_site_css.html").read_text(encoding="utf-8")
    block = css[css.index(".prov {"):css.index(".prov-detail")]
    assert "flex-wrap:wrap" in block and "max-width:100%" in block
    assert ".prov-detail { flex-basis:100%" in css
    tpl = pathlib.Path("templates/public/_provenance.html").read_text(encoding="utf-8")
    assert "width" not in tpl and "style=" not in tpl
    for label in pv.LABELS:
        assert len(label) <= 40, "a label is one short line even at 320px"
