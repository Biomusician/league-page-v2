from __future__ import annotations

import json

from leaguepage.editorial import load_coalitions, load_managers, manager_for_roster


MANAGERS = {
    "solo": {"sleeper_user_id": "1", "leagues": {"disco": {"roster_id": 1}}},
    "co-a": {"sleeper_user_id": "2", "leagues": {"surfeit": {"roster_id": 7, "co_managed": True}}},
    "co-b": {"sleeper_user_id": "3", "leagues": {"surfeit": {"roster_id": 7, "co_managed": True}}},
}


def test_manager_for_roster_solo_and_co():
    assert manager_for_roster(MANAGERS, "disco", 1) == ["solo"]
    assert sorted(manager_for_roster(MANAGERS, "surfeit", 7)) == ["co-a", "co-b"]
    assert manager_for_roster(MANAGERS, "disco", 99) == []


def test_load_managers_missing_file(tmp_path):
    assert load_managers(tmp_path / "nope.json") == {}
    assert load_coalitions(tmp_path / "nope.json")["coalitions"] == []


def test_load_managers_real_file_valid_json(tmp_path):
    path = tmp_path / "managers.json"
    path.write_text(json.dumps(MANAGERS), encoding="utf-8")
    assert load_managers(path) == MANAGERS


def test_repo_coalitions_file_is_valid():
    # coalitions.json is committed; it must always parse and carry the
    # Jonathan-supplied confirmed identity facts.
    coalitions = load_coalitions()
    keys = {c["key"] for c in coalitions["coalitions"]}
    assert {"fra-uk", "jpn-swe"} <= keys
    assert coalitions["identities"]["FRA"]["aircraft"] == "Dassault Rafale"
    assert coalitions["identities"]["SWE"]["role"] == "Gripen pilot"
    # Jonathan explicitly confirmed both roster mappings on 2026-08-29:
    # FRA/UK = surfeit roster 8, JPN/SWE = surfeit roster 7
    from leaguepage.editorial import confirmed_coalition_mappings
    mapped = {c["key"]: c["roster_mapping"]["roster_id"]
              for c in confirmed_coalition_mappings(coalitions)}
    assert mapped == {"fra-uk": 8, "jpn-swe": 7}


def test_local_managers_file_when_present():
    # managers.json is gitignored (privacy: real handles); when it exists
    # locally it must parse and keep unverified aliases out of confirmed sets.
    import pytest

    from leaguepage.config import EDITORIAL_DIR
    from leaguepage.editorial import confirmed_aliases

    if not (EDITORIAL_DIR / "managers.json").exists():
        pytest.skip("local managers.json not present (gitignored)")
    managers = load_managers()
    assert len(managers) >= 20
    for m in managers.values():
        confirmed = set(confirmed_aliases(m))
        for ua in m.get("unverified_aliases") or []:
            assert ua["name"] not in confirmed
            assert ua["status"] in ("inferred", "rejected")


def test_example_managers_file_matches_schema():
    import json

    from leaguepage.config import EDITORIAL_DIR

    example = json.loads((EDITORIAL_DIR / "managers.example.json").read_text(encoding="utf-8"))
    entry = example["example-manager"]
    for field in ("sleeper_user_id", "aliases", "unverified_aliases", "identity",
                  "sensitivity", "allow_cross_league_callbacks", "leagues"):
        assert field in entry
