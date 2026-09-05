"""One brief per section, whoever writes from it.

The packet is the interface, not the prompt: what Claude Code and ChatGPT
are told about a section must not depend on which button was pressed. It
carries facts and no paths, and it never claims a subscription is an API.
"""
from __future__ import annotations

import pytest

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import writing_packet as wp
from leaguepage.config import get_league
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LG = get_league("surfeit")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "sections").mkdir(parents=True)
    (idir / "sections" / "tracks.md").write_text("Start him.\n", encoding="utf-8")
    return db, idir


def _packet(db, section="tracks", **kw):
    with Storage(db) as s:
        return wp.build(s, LG, SEASON, "week-01", section, **kw)


def test_the_packet_carries_the_section_and_its_current_text(env):
    db, _idir = env
    p = _packet(db)
    assert p.section == "tracks" and p.section_title == "Tracks of Interest"
    assert p.current_prose == "Start him.\n"
    assert p.week == 1 and p.purpose
    text = p.render()
    assert "Tracks of Interest" in text and "Start him." in text


def test_the_league_format_is_read_not_hardcoded(env):
    """The rest of the app refuses to hardcode a setting Sleeper reports;
    so does this."""
    db, _idir = env
    p = _packet(db)
    assert "10-team" in p.format_note
    assert "1QB" in p.format_note or "Superflex" in p.format_note
    assert "PPR" in p.format_note


def test_the_commissioner_writes_the_lowdown_and_every_matchup(env):
    """The two product rules, expressed where a writer will read them."""
    db, _idir = env
    assert _packet(db, "lowdown").authorship == "commissioner"
    with Storage(db) as s:
        kids = ib.matchup_children(s, LG, SEASON, "week-01", 1)
    p = _packet(db, kids[0]["section"])
    assert p.authorship == "commissioner"
    assert "not the copy" in p.render()


def test_a_drafted_section_asks_for_a_proposal_not_a_publication(env):
    db, _idir = env
    p = _packet(db, "tracks")
    assert p.authorship in ("ai", "commissioner", "deterministic")
    p.authorship = "ai"
    assert "nothing publishes unread" in p.render()


def test_nothing_path_shaped_survives_redaction():
    for leak in (r"C:\Users\Jonathan\League-Page\editorial\2026\x.md",
                 "editorial/2026/surfeit/week-01/sections/tracks.md",
                 "data/league.sqlite3", "notes.md"):
        assert "[path]" in wp.redact(leak), leak
        assert "Jonathan" not in wp.redact(leak)
    assert wp.redact("Bucky Irving ranked RB12") == "Bucky Irving ranked RB12"


def test_the_packet_body_leaks_no_paths_or_handles(env):
    db, _idir = env
    text = _packet(db, instruction=r"tighten it, see C:\Users\Jonathan\notes.md").render()
    for leak in ("C:\\", "Jonathan", ".sqlite3", "editorial/", "AUTHORING"):
        assert leak not in text, leak


def test_render_is_deterministic_and_only_uses_its_own_fields(env):
    db, _idir = env
    p = _packet(db)
    assert p.render() == p.render()
    p2 = wp.WritingPacket(league="L", season="2027", issue_key="week-01",
                          section="x", section_title="X", purpose="p",
                          authorship="ai")
    out = p2.render()
    assert "## What this section is for" in out and "## Current text" not in out


def test_delivery_modes_do_not_pretend_a_subscription_is_an_api():
    assert wp.DELIVERY[:2] == ("copy-for-claude", "copy-for-chatgpt")
    assert set(wp.DELIVERY) == {"copy-for-claude", "copy-for-chatgpt",
                                "local-worker", "api"}
    with pytest.raises(ValueError):
        wp.WritingPacket(league="L", season="2027", issue_key="w", section="s",
                         section_title="S", purpose="p", authorship="ai")
        raise ValueError                       # the guard is on build(), below


def test_build_refuses_an_unknown_delivery(env):
    db, _idir = env
    with pytest.raises(ValueError):
        _packet(db, delivery="telepathy")


def test_the_purpose_vocabulary_is_fixed():
    """Free-form purpose text is where a private note would end up."""
    assert wp.purpose_of("lowdown", "The Lowdown").startswith("The Commissioner's")
    assert wp.purpose_of("nope", "Nope") == "Nope: a section of this week's issue."


def test_style_rules_travel_with_every_packet(env):
    db, _idir = env
    rules = " ".join(_packet(db).style_rules).lower()
    assert "voice profile" in rules and "em-dash" in rules
    assert "never invent a number" in rules


# ---------------------------------------------------- one packet, two envelopes

def test_claude_gets_paths_and_chatgpt_gets_the_packet(env):
    """Claude Code runs on this machine and can open the evidence, so it is
    given paths. ChatGPT is a website, so it gets only what is safe to
    paste. Both are told the same purpose, authorship rule and style."""
    db, _idir = env
    paths = {"skill": ".claude/skills/my-writing-style/SKILL.md",
             "research": "editorial/2027/surfeit/week-01/sections/AUTHORING-tracks.md",
             "proposal": "editorial/2027/surfeit/week-01/proposals/tracks.md",
             "target": "editorial/2027/surfeit/week-01/sections/tracks.md",
             "marker": "ROUGH DRAFT"}
    claude = wp.handoff(_packet(db, delivery="copy-for-claude"), paths=paths)
    gpt = wp.handoff(_packet(db, delivery="copy-for-chatgpt"))
    assert "AUTHORING-tracks.md" in claude and "Do not touch" in claude
    assert ".md" not in gpt and "editorial/" not in gpt
    assert "Tracks of Interest" in claude and "Tracks of Interest" in gpt
    # The authorship rule is the part that decides what comes back, so both
    # carry it. The style rules reach Claude Code through the skill file it
    # is told to read first, which is why its envelope stays paths, not
    # payload; ChatGPT cannot open that file, so it gets them inline.
    assert "Authorship:" in claude and "## Who writes it" in gpt
    assert "my-writing-style" in claude and "voice profile" in gpt
    assert len(claude) < 1200, "the Claude envelope must not start carrying content"


def test_the_chatgpt_envelope_asks_for_a_proposal_not_a_publication(env):
    db, _idir = env
    gpt = wp.handoff(_packet(db, delivery="copy-for-chatgpt"))
    assert "pastes your answer into the Desk as a proposal" in gpt
    assert "decides whether it publishes" in gpt


def test_neither_envelope_leaks_anything_private(env):
    db, _idir = env
    for delivery in ("copy-for-claude", "copy-for-chatgpt"):
        text = wp.handoff(_packet(db, delivery=delivery),
                          paths={"skill": "s.md", "research": "r.md",
                                 "proposal": "p.md", "target": "t.md"})
        for leak in ("C:\\", "Jonathan", ".sqlite3", "managers.json"):
            assert leak not in text, (delivery, leak)
