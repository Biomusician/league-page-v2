"""Sortable tables + draft-value treatment in the generated public HTML."""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups
from test_site_build import (  # reuse the synthetic two-league world
    SEASON, TEST_DISCO, TEST_SURFEIT, _build, site_env,  # noqa: F401
)


def _read(tmp, rel):
    return (tmp / "dist" / rel).read_text(encoding="utf-8")


def test_sortable_js_shipped_and_included(site_env):
    db, tmp = site_env
    _build(db, tmp)
    assert (tmp / "dist" / "assets" / "sortable.js").exists()
    html = _read(tmp, "disco/teams/index.html")
    assert "assets/sortable.js" in html


def test_teams_matrix_sortable_with_semantic_rank_values(site_env):
    db, tmp = site_env
    _build(db, tmp)
    for slug in ("disco", "surfeit"):
        html = _read(tmp, f"{slug}/teams/index.html")
        assert "data-sortable" in html
        # rank cells carry machine values so #10 never sorts before #2
        assert re.search(r'<td data-sort-value="\d+">\d+</td>', html)
        assert 'data-sort-type="number"' in html


def test_draft_board_sortable_and_missing_ref_blank(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _read(tmp, "disco/draft/index.html")
    assert "data-sortable" in html
    # picks without a reference rank get an empty sort value (sinks to bottom)
    assert 'data-sort-value=""' in html
    assert "Draft Value" in html


def test_standings_sortable_with_metric_directions(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _read(tmp, "disco/standings/index.html")
    assert "data-sortable" in html
    assert 'data-sort-dir="desc">PF' in html


def test_editorial_surfaces_not_sortable(site_env):
    db, tmp = site_env
    _build(db, tmp)
    # issue pages are editorial prose; they never opt into sorting
    html = _read(tmp, "disco/index.html")
    assert "data-sortable" not in html


def test_draft_value_treatment_and_methodology(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _read(tmp, "disco/draft/index.html")
    # league-size threshold is stated dynamically (12-team disco)
    # Collapse whitespace: the methodology paragraph is wrapped for reading
    # in the template, so a line break lands inside these phrases.
    flat = " ".join(html.split())
    assert "one full league round (12 picks here)" in flat
    assert "not whether the pick ultimately succeeds" in flat
    surf = _read(tmp, "surfeit/draft/index.html")
    assert "one full league round (10 picks here)" in " ".join(surf.split())


def test_no_confidence_or_debug_fields_in_public_output(site_env):
    db, tmp = site_env
    with Storage(db) as s:
        s.save_transactions(TEST_DISCO.league_id, 1, [{
            "transaction_id": "x1", "type": "waiver", "status": "complete",
            "adds": {"p1": 1}, "drops": {}, "roster_ids": [1],
            "settings": {"waiver_bid": 40}, "created": 9}])
    _build(db, tmp)
    html = _read(tmp, "disco/transactions/index.html")
    assert "confidence" not in html.lower()
    assert "Likely rationale" in html or "Possible rationale" in html \
        or "Rationale unclear" in html or "streaming" in html
    # inference disclaimer ships with the section
    assert "not the manager's stated intent" in html
