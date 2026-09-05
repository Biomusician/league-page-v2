"""The All-City Team sidebar feature: data guards, rule enforcement, privacy,
and the issue-builder / publish / public-build path end to end."""
from __future__ import annotations

import copy
import json

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import all_city as ac
from leaguepage.config import EDITORIAL_DIR, get_league
from leaguepage.issue_builder import assemble_issue, module_states
from leaguepage.publish import PublishError, publish_assembled_issue
from leaguepage.site_build import audit_output, build_site
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
SHIPPED = EDITORIAL_DIR / "features" / "all-city" / "2026-week-01.json"


# --------------------------------------------------------------- shipped data

def test_shipped_edition_validates():
    """The real 2026 edition is data, so it gets the same gate as any input."""
    assert ac.validate_edition(ac.load_edition(SHIPPED)) == []


def test_shipped_edition_is_a_complete_1qb_2rb_2wr_1te_1k_lineup():
    ed = ac.load_edition(SHIPPED)
    counts: dict[str, int] = {}
    for e in ed["starters"]:
        counts[e["position"]] = counts.get(e["position"], 0) + 1
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1}
    assert len({e["player"] for e in ed["starters"]}) == 7


def test_shipped_starters_are_all_cities_with_sources():
    ed = ac.load_edition(SHIPPED)
    for e in ed["starters"]:
        assert e["municipal_class"] == "city", e["player"]
        assert e["country"] in ac.COUNTRIES
        assert e["evidence"] and e["sources"], e["player"]


# ------------------------------------------------------------- the rule bites

def _edition(**overrides) -> dict:
    ed = copy.deepcopy(ac.load_edition(SHIPPED))
    ed.update(overrides)
    return ed


def test_matching_name_must_actually_be_the_players_name():
    ed = _edition()
    ed["starters"][0]["matching_name"] = "Buffalo"
    ed["starters"][0]["city"] = "Buffalo"
    errors = ac.validate_edition(ed)
    assert any("does not equal matching_name" in e for e in errors)


def test_city_must_match_the_name_exactly():
    ed = _edition()
    ed["starters"][0]["city"] = "Allentown"  # contains the name; not the name
    errors = ac.validate_edition(ed)
    assert any("not an exact match" in e for e in errors)


def test_suffixes_are_stripped_before_matching():
    assert ac._name_tokens("Travis Etienne Jr.") == ("Travis", "Etienne")
    assert ac._name_tokens("Patrick Mahomes II") == ("Patrick", "Mahomes")
    assert ac._name_tokens("Kenneth Walker III") == ("Kenneth", "Walker")


def test_a_town_or_village_never_qualifies():
    ed = _edition()
    ed["starters"][3]["municipal_class"] = "village"
    errors = ac.validate_edition(ed)
    assert any("only a city qualifies" in e for e in errors)


def test_tier_must_agree_with_the_population():
    ed = _edition()
    ed["starters"][3]["qualification"] = "marquee"  # Chase, Kansas is 396 people
    errors = ac.validate_edition(ed)
    assert any("makes this a 'technical'" in e for e in errors)


def test_country_outside_the_rule_is_rejected():
    ed = _edition()
    ed["starters"][0]["country"] = "Japan"
    assert any("outside the rule" in e for e in ac.validate_edition(ed))


# ------------------------------------------------------- malformed roster data

def test_duplicate_position_slot_is_caught():
    ed = _edition()
    ed["starters"][2]["slot"] = 1  # two RB1s
    assert any("duplicate RB slot 1" in e for e in ac.validate_edition(ed))


def test_incomplete_roster_is_caught():
    ed = _edition()
    ed["starters"] = [e for e in ed["starters"] if e["position"] != "K"]
    assert any("lineup has 0 K, the format asks for 1" in e
               for e in ac.validate_edition(ed))


def test_extra_starter_at_a_position_is_caught():
    ed = _edition()
    extra = copy.deepcopy(ed["starters"][1])
    extra["slot"] = 3
    extra["player"] = "Jonathon Brooks"
    extra["matching_name"] = extra["city"] = "Brooks"
    ed["starters"].append(extra)
    assert any("lineup has 3 RB" in e for e in ac.validate_edition(ed))


def test_position_with_no_slot_in_the_format_is_caught():
    ed = _edition()
    ed["starters"][6]["position"] = "TE"  # a second TE, format allows one
    assert any("lineup has 2 TE" in e for e in ac.validate_edition(ed))


def test_same_player_twice_is_caught():
    ed = _edition()
    ed["starters"][2]["player"] = ed["starters"][1]["player"]
    ed["starters"][2]["matching_name"] = ed["starters"][1]["matching_name"]
    ed["starters"][2]["city"] = ed["starters"][1]["city"]
    assert any("appears twice in the lineup" in e for e in ac.validate_edition(ed))


def test_missing_required_entry_fields_are_all_reported():
    ed = _edition()
    for field in ("verdict", "assessment", "state", "qualification"):
        ed["starters"][0].pop(field)
    errors = ac.validate_edition(ed)
    for field in ("verdict", "assessment", "state", "qualification"):
        assert any(f"missing '{field}'" in e for e in errors), field


def test_missing_top_level_fields_are_reported():
    ed = _edition()
    for field in ("edition", "compiled_at", "rules"):
        ed.pop(field)
    errors = ac.validate_edition(ed)
    for field in ("edition", "compiled_at", "rules"):
        assert any(f"missing required field '{field}'" in e for e in errors), field


def test_evidence_and_source_are_required():
    ed = _edition()
    ed["starters"][0]["evidence"] = []
    ed["starters"][1]["sources"] = []
    errors = ac.validate_edition(ed)
    assert any("no evidence reference" in e for e in errors)
    assert any("no source for the city claim" in e for e in errors)


def test_garbage_starters_do_not_raise():
    ed = _edition()
    ed["starters"] = ["not an object", {"player": "Nobody"}]
    assert ac.validate_edition(ed)  # errors, no exception


def test_unreadable_edition_file_is_skipped(tmp_path):
    d = tmp_path / "features" / "all-city"
    d.mkdir(parents=True)
    (d / "broken.json").write_text("{ not json", encoding="utf-8")
    assert ac.list_editions(tmp_path) == []
    assert ac.find_edition(SEASON, "week-01", "disco", base_dir=tmp_path) is None


# ------------------------------------------------------------------- privacy

PRIVATE_MARKER = "PRIVATE-CANARY-DO-NOT-PUBLISH"


def test_private_fields_never_reach_rendered_output():
    ed = _edition()
    ed["starters"][0]["research_notes"] = PRIVATE_MARKER
    ed["starters"][0]["evidence"].append(PRIVATE_MARKER)
    ed["starters"][0]["sources"][0]["url"] = PRIVATE_MARKER
    ed["starters"][0]["consensus"]["note"] = PRIVATE_MARKER
    ed["bench"][0]["note"] = PRIVATE_MARKER
    rendered = ac.render_section(ed, "Prose goes here.")
    assert PRIVATE_MARKER not in rendered


def test_rendered_output_carries_no_evidence_ids_or_urls():
    rendered = ac.render_section(ac.load_edition(SHIPPED), "Prose.")
    assert "adp:" not in rendered
    assert "http" not in rendered
    assert "research_notes" not in rendered


def test_public_and_private_field_sets_do_not_overlap():
    assert not (ac.PUBLIC_ENTRY_FIELDS & ac.PRIVATE_ENTRY_FIELDS)


# -------------------------------------------------------------------- render

def test_render_has_one_table_row_per_starter_in_format_order():
    ed = ac.load_edition(SHIPPED)
    md = ac.render_markdown(ed)
    rows = [ln for ln in md.splitlines() if ln.startswith("| ")]
    assert len(rows) == 2 + 7  # header, separator, seven starters
    positions = [r.split("|")[1].strip() for r in rows[2:]]
    assert positions == ["QB", "RB", "RB", "WR", "WR", "TE", "K"]


def test_render_prints_the_rule_and_the_valuation_sources():
    md = ac.render_markdown(ac.load_edition(SHIPPED))
    assert "Technical Qualifier" in md
    assert "FantasyPros Expert Consensus Rank" in md
    assert "2026-08-29" in md


def test_render_section_orders_table_then_prose_then_near_misses():
    ed = ac.load_edition(SHIPPED)
    out = ac.render_section(ed, "PROSE-BODY")
    assert out.index("| POS |") < out.index("PROSE-BODY") < out.index("Outside the City Limits")


def test_render_section_without_prose_still_renders():
    out = ac.render_section(ac.load_edition(SHIPPED), None)
    assert "| POS |" in out and "Outside the City Limits" in out


def test_tier_boundaries():
    assert ac.tier_for_population(104627) == "marquee"
    assert ac.tier_for_population(100000) == "marquee"
    assert ac.tier_for_population(99999) == "city"
    assert ac.tier_for_population(5006) == "city"
    assert ac.tier_for_population(5000) == "city"
    assert ac.tier_for_population(4999) == "technical"
    assert ac.tier_for_population(396) == "technical"


# ------------------------------------------------ issue builder / publish path

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    league = get_league("disco")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, league, teams=12, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
        populate_matchups(s, league, week=1, teams=12,
                          scores={rid: 100.0 + rid for rid in range(1, 13)})
    return db, league, tmp_path


def _install_edition(tmp_path, **overrides) -> dict:
    ed = _edition(**overrides)
    d = tmp_path / "editorial" / "features" / "all-city"
    d.mkdir(parents=True, exist_ok=True)
    (d / "e.json").write_text(json.dumps(ed), encoding="utf-8")
    return ed


def _carry(db, league, *keys, issue_key="week-01") -> None:
    """Put a retired section into an issue, the way an old issue has one.

    All-City and All-Marquee are no longer offered on a new issue: they
    folded into the generic custom-section primitive. An issue that already
    contains one still assembles it, still renders it and still refuses to
    publish it broken, which is what the rest of this file tests. That path
    only exists for issues carrying a saved row, so these tests write one.
    """
    with Storage(db) as s:
        for key in keys:
            s.set_issue_module(league_slug=league.slug, season=SEASON,
                               issue_key=issue_key, module_key=key, included=1)


def _module(db, league, tmp_path, issue_key="week-01") -> dict:
    _carry(db, league, "all-city", issue_key=issue_key)
    with Storage(db) as s:
        mods = module_states(s, league, SEASON, issue_key, week=1)
    return next(m for m in mods if m["module_key"] == "all-city")


def test_a_new_issue_is_never_offered_these_modules(env):
    """They are retired as future authoring concepts. Nothing here deletes
    their code, because an issue that already published one still has to
    render; they are simply not on the menu any more."""
    from leaguepage.issue_builder import (RETIRED_MODULES, WEEKLY_DEFAULT,
                                          module_defs_for)

    db, league, _tmp = env
    for key in ("all-city", "all-city-marquee"):
        assert key in RETIRED_MODULES
        assert key not in WEEKLY_DEFAULT
    offered = {k for k, _t, _kd in module_defs_for(league, "week-01")}
    assert "all-city" not in offered and "all-city-marquee" not in offered
    with Storage(db) as s:
        keys = {m["module_key"] for m in
                module_states(s, league, SEASON, "week-01", week=1)}
    assert "all-city" not in keys and "all-city-marquee" not in keys


def test_an_issue_that_already_carries_one_keeps_it(env):
    db, league, tmp = env
    m = _module(db, league, tmp)
    assert m["title"] == "The All-City Team"
    assert m["included"] is True
    # ...but it is not one of the week's jobs any more
    assert m["checklist"] is False
    assert m["retired"] is True


def test_module_is_not_ready_without_an_edition(env):
    db, league, tmp = env
    m = _module(db, league, tmp)
    assert m["status"] == "not_ready"
    assert "no all-city edition bound to" in m["detail"]


def test_module_reports_a_broken_edition_rather_than_rendering_it(env):
    db, league, tmp = env
    _install_edition(tmp)
    ed_dir = tmp / "editorial" / "features" / "all-city"
    broken = json.loads((ed_dir / "e.json").read_text(encoding="utf-8"))
    broken["starters"][0]["municipal_class"] = "town"
    (ed_dir / "e.json").write_text(json.dumps(broken), encoding="utf-8")
    m = _module(db, league, tmp)
    assert m["status"] == "needs_review"
    with Storage(db) as s:
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                           module_key="all-city", included=1, approved=1)
        assembled = assemble_issue(s, league, SEASON, "week-01",
                                   base_dir=tmp / "editorial", week=1)
    sec = next(x for x in assembled["sections"] if x["module_key"] == "all-city")
    assert sec["content_md"] is None  # a broken edition renders nothing
    assert any("The All-City Team" in w and "no publishable content" in w
               for w in assembled["warnings"])


def test_including_the_module_with_no_edition_blocks_the_publish(env):
    db, league, tmp = env
    with Storage(db) as s:
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                           module_key="all-city", included=1, approved=1)
    with pytest.raises(PublishError, match="The All-City Team"):
        _publish(db, league, tmp)


def test_edition_binds_to_one_issue_only(env):
    db, league, tmp = env
    _install_edition(tmp, issue_key="week-01")
    assert _module(db, league, tmp, "week-01")["status"] == "ready"
    assert _module(db, league, tmp, "week-02")["status"] == "not_ready"


def test_edition_respects_the_league_list(env):
    db, league, tmp = env
    _install_edition(tmp, leagues=["surfeit"])
    assert _module(db, league, tmp)["status"] == "not_ready"


def test_prose_with_a_rough_marker_blocks_publication(env):
    db, league, tmp = env
    _install_edition(tmp)
    sdir = tmp / "editorial" / SEASON / league.slug / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "all-city.md").write_text(
        "<!-- ROUGH DRAFT - COMMISSIONER EDIT REQUIRED -->\n\nWords.\n", encoding="utf-8")
    assert _module(db, league, tmp)["status"] == "drafting"
    with Storage(db) as s:
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                           module_key="all-city", included=1, approved=1)
        assembled = assemble_issue(s, league, SEASON, "week-01",
                                   base_dir=tmp / "editorial", week=1)
    assert any("blocked marker" in w for w in assembled["warnings"])


def test_edited_prose_assembles_into_the_issue(env):
    db, league, tmp = env
    _install_edition(tmp)
    sdir = tmp / "editorial" / SEASON / league.slug / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "all-city.md").write_text("Municipal paperwork, now a skill position.\n",
                                      encoding="utf-8")
    assert _module(db, league, tmp)["status"] == "edited"
    with Storage(db) as s:
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                           module_key="all-city", included=1, approved=1)
        assembled = assemble_issue(s, league, SEASON, "week-01",
                                   base_dir=tmp / "editorial", week=1)
    sec = next(x for x in assembled["sections"] if x["module_key"] == "all-city")
    assert "| POS |" in sec["content_md"]
    assert "Municipal paperwork" in sec["content_md"]
    assert "Outside the City Limits" in sec["content_md"]


def _publish(db, league, tmp, extra_modules=()):
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules",
                    "intel"):
            s.set_issue_module(league_slug=league.slug, season=SEASON,
                               issue_key="week-01", module_key=key, included=0)
        ldir = tmp / "editorial" / SEASON / league.slug / "week-01" / "lowdown"
        ldir.mkdir(parents=True, exist_ok=True)
        (ldir / "lowdown.md").write_text("# The Lowdown\n\nWeek one.\n", encoding="utf-8")
        s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                           module_key="lowdown", approved=1)
        for key in extra_modules:
            s.set_issue_module(league_slug=league.slug, season=SEASON, issue_key="week-01",
                               module_key=key, included=1, approved=1)
        return publish_assembled_issue(s, league, SEASON, "week-01", week=1,
                                       published_dir=tmp / "published",
                                       base_dir=tmp / "editorial")


def test_published_snapshot_and_public_page_carry_the_table_not_the_notes(env):
    db, league, tmp = env
    ed = _install_edition(tmp)
    ed["starters"][0]["research_notes"] = PRIVATE_MARKER
    (tmp / "editorial" / "features" / "all-city" / "e.json").write_text(
        json.dumps(ed), encoding="utf-8")
    sdir = tmp / "editorial" / SEASON / league.slug / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "all-city.md").write_text("Seven names, one residency requirement.\n",
                                      encoding="utf-8")
    snap_path = _publish(db, league, tmp, extra_modules=("all-city",))

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    sec = next(s for s in snap["sections"] if s["module_key"] == "all-city")
    assert "Josh Allen" in sec["content_md"] and PRIVATE_MARKER not in sec["content_md"]

    with Storage(db) as s:
        result = build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                            editorial_dir=tmp / "editorial")
    page = (tmp / "dist" / league.slug / SEASON / "week-01"
            / "index.html").read_text(encoding="utf-8")
    assert "The All-City Team" in page
    assert "<table>" in page and "<td>Chase, Kansas</td>" in page
    assert "Outside the City Limits" in page
    assert "Seven names, one residency requirement." in page
    assert PRIVATE_MARKER not in page
    assert audit_output(tmp / "dist", extra_forbidden=[PRIVATE_MARKER]) == []
    assert result["warnings"] == [] or all("all-city" not in w for w in result["warnings"])


def test_excluding_the_module_leaves_the_rest_of_the_issue_intact(env):
    db, league, tmp = env
    _install_edition(tmp)
    snap_path = _publish(db, league, tmp)
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    keys = [s["module_key"] for s in snap["sections"]]
    assert keys == ["lowdown"]


# ------------------------------------------------- the marquee (100k) variant

MARQUEE = EDITORIAL_DIR / "features" / "all-city-marquee" / "2026-week-01.json"
FLOOR = 100_000


def _marquee(**overrides) -> dict:
    ed = copy.deepcopy(ac.load_edition(MARQUEE))
    ed.update(overrides)
    return ed


def test_shipped_marquee_edition_validates():
    assert ac.validate_edition(ac.load_edition(MARQUEE)) == []


def test_shipped_marquee_is_a_complete_lineup():
    ed = ac.load_edition(MARQUEE)
    counts: dict[str, int] = {}
    for e in ed["starters"]:
        counts[e["position"]] = counts.get(e["position"], 0) + 1
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1}
    assert len({e["player"] for e in ed["starters"]}) == 7


def test_every_marquee_starter_clears_one_hundred_thousand():
    ed = ac.load_edition(MARQUEE)
    assert ac.minimum_population(ed) == FLOOR
    for e in ed["starters"]:
        assert isinstance(e["population"], int), e["player"]
        assert e["population"] >= FLOOR, (e["player"], e["city"], e["population"])
        assert e["municipal_class"] == "city"
        assert e["evidence"] and e["sources"], e["player"]


def test_population_floor_is_enforced():
    ed = _marquee()
    ed["starters"][0]["population"] = 99_999
    assert any("below this edition's 100,000 floor" in e
               for e in ac.validate_edition(ed))


def test_population_floor_requires_a_recorded_population():
    ed = _marquee()
    ed["starters"][0].pop("population")
    errors = ac.validate_edition(ed)
    assert any("population floor" in e and "must be recorded" in e for e in errors)


def test_population_column_requires_a_recorded_population():
    ed = _edition(columns=["pos", "player", "city", "population", "verdict"])
    ed["starters"][0].pop("population")
    assert any("the table prints population" in e for e in ac.validate_edition(ed))


def test_unknown_or_incomplete_table_columns_are_caught():
    assert any("unknown table column 'nickname'" in e
               for e in ac.validate_edition(
                   _edition(columns=["pos", "player", "city", "nickname"])))
    assert any("must include 'city'" in e
               for e in ac.validate_edition(
                   _edition(columns=["pos", "player", "verdict"])))


def test_marquee_table_prints_population_instead_of_class():
    md = ac.render_markdown(ac.load_edition(MARQUEE))
    header = md.splitlines()[0]
    assert "POPULATION" in header and "CLASS" not in header
    assert "| 104,627 |" in md          # thousands separators, not raw ints
    assert "9,089,736" in md            # Greater London


def test_an_allied_city_may_omit_state_but_a_us_city_may_not():
    ed = _marquee()
    london = next(e for e in ed["starters"] if e["city"] == "London")
    assert "state" not in london and london["country"] == "United Kingdom"
    assert ac.validate_edition(ed) == []
    ed["starters"][0].pop("state")  # Allen, Texas
    assert any("missing 'state'" in e for e in ac.validate_edition(ed))


def test_allied_city_renders_with_its_country():
    assert "London (United Kingdom)" in ac.render_markdown(ac.load_edition(MARQUEE))


def test_named_exceptions_are_marked_in_the_table_and_footnoted():
    ed = ac.load_edition(MARQUEE)
    notes = ac.exception_notes(ed)
    assert len(notes) == 1 and "District" in notes[0]
    md = ac.render_markdown(ed)
    assert "Washington, District of Columbia [1]" in md
    assert md.count("*[1] ") == 1
    assert notes[0] in md


def test_an_edition_with_no_exceptions_prints_no_footnote():
    ed = ac.load_edition(SHIPPED)
    assert ac.exception_notes(ed) == []
    assert "[1]" not in ac.render_markdown(ed)


def test_marquee_private_fields_never_reach_rendered_output():
    ed = _marquee()
    ed["starters"][0]["research_notes"] = PRIVATE_MARKER
    ed["starters"][0]["sources"][0]["url"] = PRIVATE_MARKER
    ed["bench"][0]["note"] = PRIVATE_MARKER
    rendered = ac.render_section(ed, "Prose.")
    assert PRIVATE_MARKER not in rendered
    assert "adp:" not in rendered and "http" not in rendered


def test_the_two_editions_are_separate_features_on_the_same_issue():
    """Both bind to 2026/week-01; the feature key is what keeps them apart."""
    base = ac.find_edition("2026", "week-01", "disco", feature_key="all-city")
    marq = ac.find_edition("2026", "week-01", "disco", feature_key="all-city-marquee")
    assert base["title"] == "The All-City Team"
    assert marq["title"] == "The All-Marquee Team"
    assert ac.minimum_population(base) is None
    assert ac.minimum_population(marq) == FLOOR


def test_the_floor_actually_changes_the_roster():
    """Guards the premise: if the two lineups ever converge, the second feature
    has stopped earning its place in the issue."""
    base = {e["player"] for e in ac.load_edition(SHIPPED)["starters"]}
    marq = {e["player"] for e in ac.load_edition(MARQUEE)["starters"]}
    assert base != marq
    assert len(base & marq) <= 2


def test_the_two_carried_modules_stay_independent(env):
    db, league, tmp = env
    _carry(db, league, "all-city", "all-city-marquee")
    with Storage(db) as s:
        mods = {m["module_key"]: m for m in
                module_states(s, league, SEASON, "week-01", week=1)}
    for key, title in (("all-city", "The All-City Team"),
                       ("all-city-marquee", "The All-Marquee Team")):
        assert mods[key]["title"] == title
        assert mods[key]["status"] == "not_ready"
    assert "editorial/features/all-city-marquee/" in mods["all-city-marquee"]["detail"]


def test_each_module_reads_only_its_own_feature_directory(env):
    db, league, tmp = env
    _install_edition(tmp)  # writes editorial/features/all-city/ only
    _carry(db, league, "all-city", "all-city-marquee")
    with Storage(db) as s:
        mods = {m["module_key"]: m for m in
                module_states(s, league, SEASON, "week-01", week=1)}
    assert mods["all-city"]["status"] == "ready"
    assert mods["all-city-marquee"]["status"] == "not_ready"


def test_both_features_publish_side_by_side_without_colliding(env):
    db, league, tmp = env
    _install_edition(tmp)
    d = tmp / "editorial" / "features" / "all-city-marquee"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.json").write_text(json.dumps(_marquee()), encoding="utf-8")
    sdir = tmp / "editorial" / SEASON / league.slug / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "all-city.md").write_text("Any city at all.\n", encoding="utf-8")
    (sdir / "all-city-marquee.md").write_text("One hundred thousand or bust.\n",
                                              encoding="utf-8")
    snap_path = _publish(db, league, tmp,
                         extra_modules=("all-city", "all-city-marquee"))

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert [s["module_key"] for s in snap["sections"]] == [
        "lowdown", "all-city", "all-city-marquee"]
    base_sec = next(s for s in snap["sections"] if s["module_key"] == "all-city")
    marq_sec = next(s for s in snap["sections"] if s["module_key"] == "all-city-marquee")
    assert "Any city at all." in base_sec["content_md"]
    assert "One hundred thousand or bust." in marq_sec["content_md"]
    # the parent starts Chase; the 100k floor leaves him in the near-miss list
    marq_table = marq_sec["content_md"].split("Outside the City")[0]
    assert "Chase" in base_sec["content_md"] and "Chase" not in marq_table

    with Storage(db) as s:
        build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                   editorial_dir=tmp / "editorial")
    page = (tmp / "dist" / league.slug / SEASON / "week-01"
            / "index.html").read_text(encoding="utf-8")
    assert "The All-City Team" in page and "The All-Marquee Team" in page
    assert 'id="all-city"' in page and 'id="all-city-marquee"' in page
    assert page.count("<table>") == 2
    assert audit_output(tmp / "dist") == []
