from __future__ import annotations


def test_league_roundtrip(storage):
    storage.save_league("L1", {"league_id": "L1", "name": "Disco", "season": "2026"})
    assert storage.get_league("L1")["name"] == "Disco"


def test_rosters_replaced_not_appended(storage):
    storage.save_rosters("L1", [{"roster_id": 1, "owner_id": "a"}, {"roster_id": 2, "owner_id": "b"}])
    storage.save_rosters("L1", [{"roster_id": 1, "owner_id": "a"}])
    assert len(storage.get_rosters("L1")) == 1


def test_draft_picks_roundtrip(storage):
    storage.save_draft({"draft_id": "D1", "league_id": "L1", "season": "2026", "status": "complete"})
    picks = [
        {"pick_no": 1, "round": 1, "roster_id": 3, "player_id": "p1", "picked_by": "u1"},
        {"pick_no": 2, "round": 1, "roster_id": 4, "player_id": "p2", "picked_by": "u2"},
    ]
    storage.save_draft_picks("D1", picks)
    got = storage.get_draft_picks("D1")
    assert [p["pick_no"] for p in got] == [1, 2]
    assert storage.get_drafts_for_league("L1")[0]["status"] == "complete"


def test_take_lifecycle(storage):
    take_id = storage.add_take(
        league_slug="surfeit", season="2026", week=None, source="draft-review",
        subject="team-x", quote="Deepest RB room in the league.", confidence="high",
        context="draft", author="commissioner", players=["Back One", "Back Two"],
        topic="rb-depth",
    )
    assert len(storage.open_takes("surfeit")) == 1
    # too_early keeps it in the open queue — looked at, not settled
    storage.resolve_take(take_id, "too_early")
    assert len(storage.open_takes("surfeit")) == 1
    # 'contradicted' is the pre-lifecycle vocabulary; it maps to the
    # canonical status rather than being rejected, so old rows keep meaning.
    storage.resolve_take(take_id, "contradicted", "RB room ranked 10th by week 6.")
    assert storage.open_takes("surfeit") == []
    resolved = storage.all_takes("surfeit", "2026")[0]
    # original wording survives evaluation untouched
    assert resolved["quote"] == "Deepest RB room in the league."
    assert resolved["status"] == "resolved_wrong"
    assert resolved["resolution"] == "RB room ranked 10th by week 6."
    assert resolved["resolved_at"]


def test_legacy_take_vocabulary_maps_to_the_lifecycle(storage):
    for legacy, canonical in (("validated", "resolved_right"),
                              ("contradicted", "resolved_wrong"),
                              ("retired", "void")):
        tid = storage.add_take(league_slug="disco", season="2026", week=1,
                               source="lowdown", subject="t", quote=f"q {legacy}")
        storage.resolve_take(tid, legacy)
        assert storage.get_take(tid)["status"] == canonical


def test_the_engine_recommendation_never_overwrites_the_verdict(storage):
    """The whole point of two columns: a disagreement stays visible."""
    tid = storage.add_take(league_slug="disco", season="2026", week=1,
                           source="lowdown", subject="t", quote="A claim.")
    storage.set_take_status(tid, "leaning_right")
    storage.record_take_evaluation(tid, recommended_status="leaning_wrong",
                                   evidence=["RB room now #10 of 12"])
    t = storage.get_take(tid)
    assert t["status"] == "leaning_right"
    assert t["recommended_status"] == "leaning_wrong"
    assert t["evidence"] == ["RB room now #10 of 12"]


def test_a_take_is_private_until_it_is_deliberately_made_public(storage):
    tid = storage.add_take(league_slug="disco", season="2026", week=1,
                           source="lowdown", subject="t", quote="A claim.")
    assert storage.get_take(tid)["public"] is False
    assert storage.public_takes("disco", "2026") == []
    storage.set_take_public(tid, True)
    assert [t["take_id"] for t in storage.public_takes("disco", "2026")] == [tid]
    storage.set_take_public(tid, False)
    assert storage.public_takes("disco", "2026") == []


def test_take_invalid_status_rejected(storage):
    take_id = storage.add_take(
        league_slug="disco", season="2026", week=1, source="matchup",
        subject="team-y", quote="Lock of the week.",
    )
    try:
        storage.resolve_take(take_id, "wrong", "nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_bit_usage_log(storage):
    storage.log_bit_usage(
        manager_key="manager-a", bit="cowboys-fan", league_slug="disco",
        season="2026", week=1,
    )
    recent = storage.recent_bit_usage("manager-a")
    assert recent[0]["bit"] == "cowboys-fan"
    assert storage.recent_bit_usage("someone-else") == []
