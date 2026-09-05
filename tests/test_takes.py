"""Takes: inference, lifecycle, evidence, and the two boundaries that matter.

The boundaries this file exists to defend:

* **Over-judging.** A take that loses one week is not wrong. Every hook has a
  sample floor and every take can carry a horizon, and both answer TOO EARLY
  rather than guessing.
* **Provenance.** A paraphrase must never be presented as a quotation, and a
  receipt without its issue must never reach a reader.
"""
from __future__ import annotations

import pytest

from leaguepage import pubqa, takes
from leaguepage.config import get_league
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

LEAGUE = get_league("surfeit")
NAMES = {1: "Los Bandidos (Bandit)", 2: "Wild SeeKats (Seebass)", 3: "Dave",
         4: "Gary", 5: "Swanson"}
TOKENS = {rid: pubqa._norm_tokens(nm) for rid, nm in NAMES.items()}
SLUGS = {rid: f"t{rid}" for rid in NAMES}
PLAYERS = {"Josh Jacobs": "RB", "Jake Bates": "K", "Buffalo Bills": "DEF",
           "Bijan Robinson": "RB", "Juwan Johnson": "TE"}


# ------------------------------------------------------------- inference

@pytest.mark.parametrize("quote,topic", [
    ("Los Bandidos should win this matchup comfortably.", "matchup"),
    ("They claimed Kaleb Johnson off waivers to fix it.", "trade"),
    ("Gary misses the playoffs with this roster.", "playoff"),
    ("Jordan James at pick 123 against a consensus rank of 254.", "draft"),
    ("The RB room is thin and it will cost them.", "roster"),
    ("Dave is the best team in the league on paper.", "power"),
    ("Everyone had a very nice time and nobody argued.", "other"),
])
def test_topic_inference(quote, topic):
    assert takes.infer_topic(quote) == topic


def test_subject_inference_names_one_team():
    got = takes.infer_subject("Swanson drafted zero kickers.", name_tokens=TOKENS,
                              public_names=NAMES, slugs=SLUGS)
    assert got["subject_type"] == "team" and got["subject_roster_id"] == 5


def test_two_teams_named_is_a_matchup_not_a_subject():
    got = takes.infer_subject("Swanson play Gary in week one.", name_tokens=TOKENS,
                              public_names=NAMES, slugs=SLUGS)
    assert got["subject_type"] == "matchup"
    assert got["subject_roster_id"] is None


def test_no_team_named_leaves_the_subject_for_the_commissioner():
    got = takes.infer_subject("Somebody is going to regret this.",
                              name_tokens=TOKENS, public_names=NAMES, slugs=SLUGS)
    assert got["subject_type"] is None


def test_subject_from_heading_attributes_a_capsule():
    md = ("### 1. Swanson (-137)\n\nThe RB room is thin and it will cost them.\n\n"
          "### 2. Gary (-189)\n\nA clean board with one exception.\n")
    assert takes.subject_from_heading(
        "The RB room is thin and it will cost them.", md, name_tokens=TOKENS) == 5
    assert takes.subject_from_heading(
        "A clean board with one exception.", md, name_tokens=TOKENS) == 4


def test_player_inference_handles_surnames_but_not_defenses():
    assert "Josh Jacobs" in takes.infer_players("Jacobs is gone already.", PLAYERS)
    # "three Bills" is three Buffalo players, not the Buffalo defense
    assert takes.infer_players("They hold three Bills.", PLAYERS) == []


@pytest.mark.parametrize("horizon,created,expected", [
    ("next-week", 4, 5), ("3-weeks", 4, 7), ("midseason", 1, 7),
    ("end-of-season", 1, 15), ("playoffs", 1, 15), ("manual", 4, None),
    (None, 4, None),
])
def test_review_week(horizon, created, expected):
    assert takes.review_week_for(horizon, created_week=created,
                                 playoff_week_start=15) == expected


# ---------------------------------------------------------- over-judging

def ctx(**over):
    base = dict(week=6, weeks_played=6, n_teams=10, public_names=NAMES,
                positional_ranks={1: {"RB": 10, "WR": 1}},
                opening_ranks={1: {"RB": 5}}, records={},
                all_play={}, model_rank={1: 1}, playoff={}, moves={},
                roster_of_player={}, starts={}, points={}, matchup_results={})
    base.update(over)
    return base


def take(**over):
    base = {"take_id": 1, "status": "open", "topic": "roster",
            "quote": "The RB room is thin and can sink this roster.",
            "subject_roster_id": 1, "players": [], "review_week": None}
    base.update(over)
    return base


def test_a_positional_claim_is_too_early_from_two_games():
    r = takes.evaluate_take(take(), ctx(weeks_played=2, week=2))
    assert r["recommended_status"] == takes.TOO_EARLY
    assert "need" in r["why"]


def test_the_same_claim_leans_right_once_the_sample_exists():
    r = takes.evaluate_take(take(), ctx())
    assert r["recommended_status"] == takes.LEANING_RIGHT
    assert any("RB room now ranks #10" in e for e in r["evidence"])
    assert any("was #5 at publication" in e for e in r["evidence"])


def test_a_worry_that_did_not_happen_leans_wrong():
    r = takes.evaluate_take(take(), ctx(positional_ranks={1: {"RB": 2}}))
    assert r["recommended_status"] == takes.LEANING_WRONG
    assert r["why"] == "evidence is moving against this take"


def test_a_review_horizon_holds_a_verdict_back():
    r = takes.evaluate_take(take(review_week=9), ctx(week=6))
    assert r["recommended_status"] == takes.TOO_EARLY
    assert "week 9" in r["why"]


def test_a_settled_take_is_never_re_judged():
    r = takes.evaluate_take(take(status=takes.RESOLVED_RIGHT), ctx())
    assert r["recommended_status"] == takes.RESOLVED_RIGHT
    assert "Commissioner" in r["why"]


def test_evidence_without_a_direction_stays_open():
    r = takes.evaluate_take(take(quote="The RB room exists."), ctx(
        positional_ranks={1: {"RB": 5}}))
    assert r["recommended_status"] == takes.OPEN
    assert r["evidence"]


# ------------------------------------------------------ evidence hooks

def test_a_draft_claim_reads_starts_not_the_reach_classification():
    """REACH/STEAL is immutable market analysis; what is testable is whether
    the player is here and playing."""
    praised = take(topic="draft", players=["Bijan Robinson"],
                   quote="Bijan Robinson was the steal of this draft.")
    good = takes.evaluate_take(praised, ctx(
        roster_of_player={"Bijan Robinson": 1}, starts={"Bijan Robinson": 6},
        points={"Bijan Robinson": 120.5}))
    assert good["recommended_status"] == takes.LEANING_RIGHT
    assert any("started 6 of 6" in e for e in good["evidence"])
    assert not any("REACH" in e or "STEAL" in e for e in good["evidence"])

    benched = takes.evaluate_take(praised, ctx(
        roster_of_player={"Bijan Robinson": 1}, starts={}, points={}))
    assert benched["recommended_status"] == takes.LEANING_WRONG


def test_a_departed_player_moves_a_praising_draft_claim():
    r = takes.evaluate_take(
        take(topic="draft", players=["Josh Jacobs"],
             quote="Josh Jacobs was the steal of the draft."),
        ctx(roster_of_player={}))
    assert r["recommended_status"] == takes.LEANING_WRONG
    assert any("not on any roster" in e for e in r["evidence"])


def test_a_matchup_prediction_resolves_on_the_result():
    right = takes.evaluate_take(
        take(topic="matchup", quote="Los Bandidos win this one."),
        ctx(matchup_results={1: {"week": 4, "won": True,
                                 "line": "Los Bandidos 120 – 99 Gary"}}))
    assert right["recommended_status"] == takes.RESOLVED_RIGHT
    wrong = takes.evaluate_take(
        take(topic="matchup", quote="Los Bandidos win this one."),
        ctx(matchup_results={1: {"week": 4, "won": False,
                                 "line": "Los Bandidos 99 – 120 Gary"}}))
    assert wrong["recommended_status"] == takes.RESOLVED_WRONG


def test_a_power_claim_uses_the_model_board():
    strong = takes.evaluate_take(
        take(topic="power", quote="Los Bandidos are the best team in the league."),
        ctx(model_rank={1: 1}))
    assert strong["recommended_status"] == takes.LEANING_RIGHT
    weak = takes.evaluate_take(
        take(topic="power", quote="Los Bandidos are the best team in the league."),
        ctx(model_rank={1: 9}))
    assert weak["recommended_status"] == takes.LEANING_WRONG


def test_a_transaction_claim_reads_the_rank_shift():
    fixed = takes.evaluate_take(
        take(topic="trade", quote="That claim fixes their RB problem."),
        ctx(moves={1: [{"line": "Claimed X", "rank_shift": "RB #10 → #6",
                        "outcome": None, "questionable": False}]}))
    assert fixed["recommended_status"] == takes.LEANING_RIGHT
    didnt = takes.evaluate_take(
        take(topic="trade", quote="That claim fixes their RB problem."),
        ctx(moves={1: [{"line": "Claimed X", "rank_shift": "RB #6 → #10",
                        "outcome": None, "questionable": True}]}))
    assert didnt["recommended_status"] == takes.LEANING_WRONG
    assert any("questionable" in e for e in didnt["evidence"])


def test_a_playoff_claim_waits_for_six_weeks():
    early = takes.evaluate_take(
        take(topic="playoff", quote="Los Bandidos make the playoffs."),
        ctx(weeks_played=4, week=4, playoff={1: "71% (likely)"}))
    assert early["recommended_status"] == takes.TOO_EARLY
    later = takes.evaluate_take(
        take(topic="playoff", quote="Los Bandidos make the playoffs."),
        ctx(weeks_played=8, week=8, playoff={1: "71% (likely)"}))
    assert later["recommended_status"] == takes.LEANING_RIGHT


def test_evidence_is_never_a_punchline():
    """The deterministic layer supplies evidence; the joke is his."""
    for t in (take(), take(topic="power",
                           quote="Los Bandidos are the worst team in the league.")):
        r = takes.evaluate_take(t, ctx())
        text = " ".join(r["evidence"] + [r["why"]]).lower()
        for tell in ("aged like", "should retire", "embarrassing", "clown",
                     "lol", "disaster", "idiot"):
            assert tell not in text


# ---------------------------------------------------------- public view

def public_take(**over):
    base = {"take_id": 7, "public": True, "status": takes.LEANING_WRONG,
            "recommended_status": takes.LEANING_WRONG, "verbatim": True,
            "quote": "RB depth is the assumption that can break this roster.",
            "evidence": ["RB room now ranks #10 of 10"], "href": "2026/draft/index.html",
            "issue_key": "draft", "week": None, "subject_roster_id": 1}
    base.update(over)
    return base


def test_a_private_take_never_becomes_a_receipt():
    assert takes.public_receipt(public_take(public=False), names=NAMES) is None


def test_a_receipt_needs_evidence_and_provenance():
    assert takes.public_receipt(public_take(evidence=[]), names=NAMES) is None
    assert takes.public_receipt(public_take(href=None), names=NAMES) is None
    assert takes.public_receipt(public_take(issue_key=None), names=NAMES) is None


def test_an_unmoved_take_is_not_a_receipt():
    assert takes.public_receipt(
        public_take(status=takes.OPEN, recommended_status=takes.OPEN),
        names=NAMES) is None
    assert takes.public_receipt(
        public_take(status=takes.OPEN, recommended_status=takes.TOO_EARLY),
        names=NAMES) is None


def test_the_commissioners_verdict_outranks_the_engine():
    r = takes.public_receipt(
        public_take(status=takes.RESOLVED_RIGHT,
                    recommended_status=takes.LEANING_WRONG), names=NAMES)
    assert r["status"] == "AGING WELL" and r["settled"] is True


def test_a_paraphrase_is_never_framed_as_a_quotation():
    r = takes.public_receipt(public_take(verbatim=False), names=NAMES)
    assert r["verbatim"] is False
    assert r["attribution"] == "wrote, in substance"


def test_public_status_wording_stays_neutral():
    assert set(takes.PUBLIC_STATUS.values()) == {"AGING WELL", "UNDER PRESSURE",
                                                 "BUSTED"}


# ------------------------------------------------------------- QA gate

def qa_ctx(**over):
    c = pubqa.QAContext(league_slug="surfeit", season="2026", issue_key="draft",
                        public_names=NAMES, n_teams=5)
    c.name_tokens = TOKENS
    for k, v in over.items():
        setattr(c, k, v)
    return c


def test_qa_blocks_a_receipt_without_provenance():
    fs = pubqa.check_receipts([{"quote": "A claim.", "href": "", "issue_key": ""}],
                              qa_ctx())
    assert fs and all(f.severity == pubqa.BLOCKER for f in fs)
    assert "missing provenance" in fs[0].title


def test_qa_blocks_a_paraphrase_dressed_as_a_quote():
    fs = pubqa.check_receipts(
        [{"quote": "A claim.", "href": "x", "issue_key": "draft",
          "verbatim": False, "presented_as_quote": True}], qa_ctx())
    assert any(f.title == "Paraphrase presented as a quotation" for f in fs)


def test_qa_blocks_private_fields_travelling_with_a_receipt():
    fs = pubqa.check_receipts(
        [{"quote": "A claim.", "href": "x", "issue_key": "draft",
          "note": "he was drunk when he wrote this"}], qa_ctx())
    assert any(f.privacy for f in fs)


def test_qa_blocks_a_private_handle_inside_a_receipt():
    fs = pubqa.check_receipts(
        [{"quote": "confedfatties said it first.", "href": "x",
          "issue_key": "draft"}], qa_ctx(private_handles=["confedfatties"]))
    priv = [f for f in fs if f.privacy]
    assert priv and "confedfatties" not in (priv[0].excerpt or "")


def test_qa_blocks_an_unresolved_identity_in_a_receipt():
    fs = pubqa.check_receipts(
        [{"quote": "Roster 4 will regret this.", "href": "x",
          "issue_key": "draft"}], qa_ctx())
    assert any(f.title == "Unresolved identity inside a receipt" for f in fs)


def test_qa_warns_on_a_stale_team_name_in_a_receipt():
    fs = pubqa.check_receipts(
        [{"quote": "A claim.", "href": "x", "issue_key": "draft",
          "subject_name": "Jesse"}], qa_ctx())
    stale = [f for f in fs if f.title == "Receipt names a stale team"]
    assert stale and stale[0].severity == pubqa.WARNING


def test_a_clean_receipt_passes():
    assert pubqa.check_receipts(
        [{"quote": "RB depth can break this roster.", "href": "2026/draft/index.html",
          "issue_key": "draft", "verbatim": True, "presented_as_quote": True,
          "subject_name": "Dave"}], qa_ctx()) == []


# --------------------------------------------------- retroactive capture

SNAP = {
    "issue_key": "draft", "issue_label": "Draft Issue",
    "href": "2026/draft/index.html",
    "sections": [{
        "module_key": "custom", "title": "Draft Power Rankings",
        "content_md": (
            "## Draft Power Rankings\n\n"
            "### 1. Swanson (-137)\n\n"
            "The RB room is thin and can sink this roster before Halloween.\n\n"
            "### 2. Gary (-189)\n\n"
            "Jason Myers 95 picks early is the single largest kicker premium "
            "in the league, and at this table that is a contested title.\n\n"
            "### 3. Dave (+1)\n\n"
            "The full draft board is on the draft page for anyone who cares.\n\n"
            "We are a league that pays premium rates for commodity "
            "capabilities, and the invoices arrive in December.\n"),
    }],
}
SNAP_TOKENS = {3: {"dave"}, 4: {"gary"}, 5: {"swanson"}}
SNAP_NAMES = {3: "Dave", 4: "Gary", 5: "Swanson"}
SNAP_SLUGS = {3: "dave", 4: "gary", 5: "swanson"}


def scan(**over):
    kw = dict(name_tokens=SNAP_TOKENS, public_names=SNAP_NAMES, slugs=SNAP_SLUGS,
              player_positions={"Jason Myers": "K"})
    kw.update(over)
    return takes.candidate_takes(SNAP, **kw)


def test_candidates_find_the_real_claim_and_attribute_it_by_heading():
    cands = scan()
    quotes = [c["quote"] for c in cands]
    assert any("can sink this roster" in q for q in quotes)
    top = next(c for c in cands if "can sink" in c["quote"])
    assert top["subject_name"] == "Swanson"
    assert top["reasons"] and top["href"] == "2026/draft/index.html"


def test_candidates_never_offer_a_kicker_premium_claim():
    """The calibration decision, enforced at capture: a special-teams
    'premium' measures the reference board, not a roster decision."""
    assert not any("kicker premium" in c["quote"] for c in scan())


def test_candidates_skip_signposting():
    assert not any("draft page" in c["quote"] for c in scan())


def test_a_league_wide_line_is_not_attributed_to_the_last_team():
    for c in scan():
        if "commodity capabilities" in c["quote"]:
            assert c["subject_name"] != "Dave"


def test_already_tracked_quotes_are_not_offered_again():
    first = scan()
    assert first
    again = scan(existing_quotes={first[0]["quote"]})
    assert first[0]["quote"] not in [c["quote"] for c in again]


def test_near_duplicates_collapse():
    a = "The RB room is thin and can sink this roster before Halloween."
    assert takes._near_duplicate(a, a)
    assert takes._near_duplicate(
        a, "The RB room is thin and can sink this roster before November.")
    assert not takes._near_duplicate(a, "Gary drafted a kicker far too early.")


def test_candidate_output_is_capped():
    assert len(scan()) <= takes.MAX_CANDIDATES


# ------------------------------------------------------- live integration

@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LEAGUE, teams=10, rounds=3, picks="complete",
                        season="2026")
        s.set_meta("current_week", "5")
        for wk in range(1, 5):
            populate_matchups(s, LEAGUE, week=wk, teams=10,
                              scores={rid: 90.0 + rid + wk for rid in range(1, 11)})
    with Storage(db) as s:
        yield s


def test_evaluate_all_persists_and_leaves_the_verdict_alone(env):
    tid = takes.create_take(env, LEAGUE, "2026",
                            quote="The RB room is thin and can sink this roster.",
                            issue_key="draft", section="lowdown",
                            subject_roster_id=1, subject_type="team",
                            topic="roster")
    out = takes.evaluate_all(env, LEAGUE, "2026", 5)
    assert len(out) == 1
    stored = env.get_take(tid)
    assert stored["last_evaluated_at"]
    assert stored["recommended_status"] in takes.STATUS_LABELS
    assert stored["status"] == "open", "evaluation must not move the verdict"


def test_evaluate_all_is_a_no_op_with_no_takes(env):
    assert takes.evaluate_all(env, LEAGUE, "2026", 5) == []


def test_create_take_records_provenance_and_horizon(env):
    tid = takes.create_take(env, LEAGUE, "2026", quote="Dave win the title.",
                            issue_key="week-02", section="lowdown", week=2,
                            review_after="3-weeks", href="2026/week-02/index.html",
                            playoff_week_start=15)
    t = env.get_take(tid)
    assert t["issue_key"] == "week-02" and t["source"] == "lowdown"
    assert t["href"] == "2026/week-02/index.html"
    assert t["review_week"] == 5
    assert t["status"] == "open" and t["public"] is False


def test_a_take_needs_a_quote(env):
    with pytest.raises(ValueError):
        takes.create_take(env, LEAGUE, "2026", quote="   ", issue_key="draft",
                          section="lowdown")


def test_a_draft_take_is_never_judged_on_a_kickers_starts():
    """Kickers start every week by definition, so "did he start" says nothing
    about a claim. Named, reported, never the verdict."""
    only_kicker = take(topic="draft", players=["Jake Bates"],
                       quote="Jake Bates was the steal of this draft.")
    r = takes.evaluate_take(only_kicker, ctx(
        player_positions={"Jake Bates": "K"},
        roster_of_player={"Jake Bates": 1}, starts={"Jake Bates": 6}))
    assert r["recommended_status"] == takes.OPEN
    assert any("Jake Bates" in e for e in r["evidence"]), "still reported"


def test_a_mixed_take_is_judged_on_the_skill_player_only():
    mixed = take(topic="draft", players=["Bijan Robinson", "Jake Bates"],
                 quote="Bijan Robinson and Jake Bates were the steals here.")
    r = takes.evaluate_take(mixed, ctx(
        player_positions={"Bijan Robinson": "RB", "Jake Bates": "K"},
        roster_of_player={"Jake Bates": 1}, starts={"Jake Bates": 6}))
    # Bijan is gone; the kicker's six starts must not rescue the claim
    assert r["recommended_status"] == takes.LEANING_WRONG
