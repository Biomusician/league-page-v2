"""Evidence reference scheme — the provenance layer for everything editorial.

Every factual claim that reaches a dossier, story candidate, award nomination,
or editorial packet carries one or more evidence IDs. The scheme is plain
strings so they serialize anywhere and can be resolved back to data later
(Matchup Lab reuses this for weekly evidence).

Formats:
    sleeper:league:{league_id}
    sleeper:pick:{draft_id}:{pick_no}
    sleeper:roster:{league_id}:{roster_id}
    sleeper:matchup:{league_id}:{week}:{matchup_id}      (reserved for Matchup Lab)
    adp:{source_key}:{normalized_player_name}
    computed:{metric}:{league_slug}:{season}:{scope}
    archive:issue:{issue_id}
    editorial:manager:{manager_key}
    editorial:coalition:{coalition_key}
    take:{take_id}
"""
from __future__ import annotations

from leaguepage.names import normalize_name


def pick_ref(draft_id: str, pick_no: int) -> str:
    return f"sleeper:pick:{draft_id}:{pick_no}"


def league_ref(league_id: str) -> str:
    return f"sleeper:league:{league_id}"


def roster_ref(league_id: str, roster_id: int) -> str:
    return f"sleeper:roster:{league_id}:{roster_id}"


def adp_ref(source_key: str, player_name: str) -> str:
    return f"adp:{source_key}:{normalize_name(player_name)}"


def computed_ref(metric: str, league_slug: str, season: str, scope: str) -> str:
    return f"computed:{metric}:{league_slug}:{season}:{scope}"


def archive_ref(issue_id: int) -> str:
    return f"archive:issue:{issue_id}"


def manager_ref(manager_key: str) -> str:
    return f"editorial:manager:{manager_key}"


def coalition_ref(coalition_key: str) -> str:
    return f"editorial:coalition:{coalition_key}"


def take_ref(take_id: int) -> str:
    return f"take:{take_id}"
