"""Force Flow leads with who did it, and is not an archive section.

Two product rules from the same tranche.

**Team first.** A reader's opening question about any move is which team
made it. Every surface on the tab -- the Commissioner's selections, the
machine reading, the log -- now leads with the team, resolved from roster
ids to canonical public names and linked. Nothing recognises a team from
the prose written about a move.

**Not archived.** Force Flow became a standing tab. Issues that carried it
as a weekly section still hold that prose in their immutable snapshots; the
archived issue no longer displays it, and the tab reads the structured
selections behind it instead. A presentation rule at render time, never a
rewrite of a published file.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from leaguepage.force_flow_history import PERSISTENT_TAB_MODULES
from leaguepage.publish import publish_assembled_issue
from leaguepage.storage import Storage
from leaguepage.transaction_analysis import analyze_transactions, story_candidate_id

from fixtures import add_players
from test_site_build import _build, site_env, SEASON, TEST_SURFEIT  # noqa: F401

LG = TEST_SURFEIT
LID = LG.league_id


def _tx(db, *txns):
    with Storage(db) as s:
        add_players(s, {"TX1": ("Log Player", "RB", 500),
                        "TX2": ("Swap Out", "WR", 400),
                        "TX3": ("Other Side", "TE", 300)})
        s.save_transactions(LID, 1, list(txns))


WAIVER = {"transaction_id": "t1", "type": "waiver", "status": "complete", "leg": 1,
          "adds": {"TX1": 3}, "drops": {}, "waiver_budget": [{"amount": 40}],
          "settings": {"waiver_bid": 40}, "created": 5}
TRADE = {"transaction_id": "t2", "type": "trade", "status": "complete", "leg": 1,
         "adds": {"TX2": 1, "TX3": 2}, "drops": {"TX2": 2, "TX3": 1}, "created": 6}


def _page(tmp, rel="transactions/index.html"):
    return (tmp / "dist" / "surfeit" / rel).read_text(encoding="utf-8")


def _cards(html, section):
    start = html.index(section)
    block = html[start:]
    end = block.find("</section>")
    block = block[:end]
    return re.findall(r'<article class="card move">(.*?)</article>', block, re.S)


def _publish_with_force_flow(db, tmp, prose, *, decisions=()):
    """A weekly issue carrying a Force Flow section, the old way."""
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "blackbox",
                    "false-assumptions", "branches"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=0)
        idir = tmp / "editorial" / SEASON / "surfeit" / "week-01"
        (idir / "lowdown").mkdir(parents=True, exist_ok=True)
        (idir / "sections").mkdir(parents=True, exist_ok=True)
        (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nWords.\n", encoding="utf-8")
        (idir / "sections" / "forceflow.md").write_text(prose, encoding="utf-8")
        for key in ("lowdown", "forceflow"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=1, approved=1)
        for cid, note in decisions:
            s.set_story_decision(league_slug="surfeit", season=SEASON, workflow="week-01",
                                 candidate_id=cid, decision="include", note=note)
        return publish_assembled_issue(s, LG, SEASON, "week-01", week=1,
                                       published_dir=tmp / "published",
                                       base_dir=tmp / "editorial")


def _candidate_ids(db):
    with Storage(db) as s:
        rows = analyze_transactions(s, LG, 18)
    return {r["txn_id"]: story_candidate_id(r) for r in rows}


# ============================================================== team first

def test_a_single_team_move_leads_with_the_team(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _build(db, tmp)
    html = _page(tmp)
    cards = _cards(html, "Reading the Moves")
    assert cards, "a 40-FAAB claim is a meaningful move"
    card = cards[0]
    mover = re.search(r'<h4 class="mover">(.*?)</h4>', card, re.S).group(1)
    assert "Team 3" in mover
    assert card.index('class="mover"') < card.index("Claimed Log Player")


def test_the_team_is_a_link_to_its_canonical_page(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    with Storage(db) as s:
        s.set_public_team_name("surfeit", 3, "The Third Estate")
    _build(db, tmp)
    html = _page(tmp)
    card = _cards(html, "Reading the Moves")[0]
    mover = re.search(r'<h4 class="mover">(.*?)</h4>', card, re.S).group(1)
    m = re.search(r'<a href="\.\./team/([a-z0-9-]+)/index\.html">The Third Estate</a>', mover)
    assert m, mover
    assert (tmp / "dist" / "surfeit" / "team" / m.group(1) / "index.html").exists()
    assert "Team 3" not in mover


def test_a_trade_names_both_sides(site_env):
    db, tmp = site_env
    _tx(db, TRADE)
    _build(db, tmp)
    html = _page(tmp)
    card = _cards(html, "Reading the Moves")[0]
    mover = re.search(r'<h4 class="mover">(.*?)</h4>', card, re.S).group(1)
    assert "Team 1" in mover and "Team 2" in mover
    assert "↔" in mover
    assert mover.count("<a href=") == 2
    assert card.index('class="mover"') < card.index("Trade:")


def test_the_log_puts_the_team_beside_the_week(site_env):
    db, tmp = site_env
    _tx(db, WAIVER, TRADE)
    _build(db, tmp)
    html = _page(tmp)
    head = re.search(r"<tr><th[^>]*>Week</th>\s*<th[^>]*>Team</th>\s*<th[^>]*>Move</th>", html)
    assert head, "Week | Team | Move | Added | Dropped | FAAB"
    log = html[html.index("Transaction Log"):]
    # the team cell is still the row header, now in the second column
    rows = re.findall(r'<tr><td>1</td><th scope="row" class="wrap">(.*?)</th><td>([a-z ]+)</td>',
                      log, re.S)
    assert len(rows) == 2
    kinds = {kind for _t, kind in rows}
    assert kinds == {"waiver", "trade"}
    trade_cell = next(t for t, kind in rows if kind == "trade")
    assert "↔" in trade_cell and trade_cell.count("<a href=") == 2


def test_sort_semantics_survive_the_reorder(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _build(db, tmp)
    html = _page(tmp)
    head = html[html.index("Transaction Log"):]
    head = head[:head.index("</tr>")]
    assert 'data-sort-type="number" data-sort-dir="desc">Week' in head
    assert 'data-sort-type="text">Team' in head
    assert 'data-sort-type="number" data-sort-dir="desc">FAAB' in head


# ================================================ moves that mattered

def test_selections_reach_the_tab_as_structured_team_identity(site_env):
    """The prose deliberately names the WRONG team. If the page believed
    the sentence, Team 9 would be the mover; it reads the decision's
    transaction instead and finds Team 3."""
    db, tmp = site_env
    _tx(db, WAIVER)
    cid = _candidate_ids(db)["t1"]
    assert cid == "txn:Week 1: Log Player (waiver)"
    _publish_with_force_flow(db, tmp, "• Team 9 claimed Log Player off waivers (wk 1)\n",
                             decisions=[(cid, None)])
    _build(db, tmp)
    html = _page(tmp)
    block = html[html.index("Moves That Mattered"):html.index("Reading the Moves")]
    card = re.search(r'<article class="card move">(.*?)</article>', block, re.S).group(1)
    mover = re.search(r'<h4 class="mover">(.*?)</h4>', card, re.S).group(1)
    assert "Team 3" in mover and "<a href=" in mover
    assert "Team 9" not in block
    assert "Claimed Log Player" in card
    assert "Week 01" in block and f"{SEASON} Week 01" in block


def test_a_commissioner_note_survives(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    cid = _candidate_ids(db)["t1"]
    with Storage(db) as s:
        s.set_force_flow_note(league_slug="surfeit", season=SEASON, txn_id="t1",
                              note="He said he wanted a bruiser.")
    _publish_with_force_flow(db, tmp, "prose\n", decisions=[(cid, None)])
    _build(db, tmp)
    html = _page(tmp)
    block = html[html.index("Moves That Mattered"):html.index("Reading the Moves")]
    assert "<b>Commissioner:</b> He said he wanted a bruiser." in block


def test_rationale_stays_labelled_as_inference(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    cid = _candidate_ids(db)["t1"]
    _publish_with_force_flow(db, tmp, "prose\n", decisions=[(cid, None)])
    _build(db, tmp)
    block = _page(tmp)
    block = block[block.index("Moves That Mattered"):block.index("Reading the Moves")]
    assert "never a manager's stated intent" in block


def test_an_issue_with_prose_but_no_selection_shows_the_prose_as_published(site_env):
    """The fallback, and the whole extent of it."""
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "The old section, verbatim.\n")
    _build(db, tmp)
    html = _page(tmp)
    block = html[html.index("Moves That Mattered"):html.index("Reading the Moves")]
    assert "The old section, verbatim." in block
    assert 'class="mover"' not in block


def test_a_selected_move_missing_from_the_log_is_not_invented(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "prose\n",
                             decisions=[("txn:Week 1: Ghost Player (waiver)", None)])
    _build(db, tmp)
    html = _page(tmp)
    block = html[html.index("Moves That Mattered"):html.index("Reading the Moves")]
    assert "Ghost Player" not in block
    assert "prose" in block, "nothing structured matched, so the issue's prose stands"


def test_nothing_published_means_no_editorial_section(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _build(db, tmp)
    assert "Moves That Mattered" not in _page(tmp)


# ============================================= not an archive section

def test_the_archived_issue_omits_force_flow(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "Retired section prose.\n")
    _build(db, tmp)
    issue = _page(tmp, f"{SEASON}/week-01/index.html")
    assert 'id="lowdown"' in issue
    assert 'id="forceflow"' not in issue
    assert ">Force Flow</h2>" not in issue
    assert "Retired section prose." not in issue


def test_the_snapshot_is_not_rewritten_to_achieve_that(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "Retired section prose.\n")
    path = tmp / "published" / "surfeit" / SEASON / "week-01.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    _build(db, tmp)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    snap = json.loads(path.read_text(encoding="utf-8"))
    keys = [s["module_key"] for s in snap["sections"]]
    assert "forceflow" in keys, "the record keeps what was published"
    assert PERSISTENT_TAB_MODULES == {"forceflow"}


def test_the_home_page_issue_contents_omit_it_too(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "Retired section prose.\n")
    _build(db, tmp)
    home = _page(tmp, "index.html")
    contents = home[home.index("In this issue"):]
    contents = contents[:contents.index("</section>")]
    assert "The Lowdown" in contents
    assert "Force Flow" not in contents


def test_the_prose_appears_once_on_the_tab_and_nowhere_in_the_archive(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "Retired section prose.\n")
    _build(db, tmp)
    assert _page(tmp).count("Retired section prose.") == 1
    assert "Retired section prose." not in _page(tmp, f"{SEASON}/week-01/index.html")


def test_the_archive_index_has_no_force_flow_entry(site_env):
    db, tmp = site_env
    _tx(db, WAIVER)
    _publish_with_force_flow(db, tmp, "Retired section prose.\n")
    _build(db, tmp)
    archive = _page(tmp, "archive/index.html")
    body = archive[archive.index('<main id="content">'):archive.index("</main>")]
    assert "Force Flow" not in body
    assert "transactions/index.html" not in body
