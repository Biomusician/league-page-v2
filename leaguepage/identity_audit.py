"""Does every owner line up across the stores that describe them?

Four stores each hold part of an owner's identity and none of them is a
foreign key to the others:

  Sleeper `league_users`   user_id, display name, fantasy team name
  Sleeper `rosters`        roster_id, owner_id, co_owners
  `team_names`            the public display name the Commissioner confirmed
  `editorial/managers.json` callsign, aliases, per-league roster binding

They are joined on the Sleeper user id, and nothing until now checked that
the join actually holds. A roster can change hands, a manager can rename a
team, a callsign can be edited into a public name and nowhere else, and
every one of those reads as normal on every screen.

So this is a reconciliation, not a spell-check: every finding names the two
stores that disagree and the stable id they disagree about. It never
guesses that two similar strings are the same person -- deciding that is a
factual claim, and the one place it is allowed to be made is the
Commissioner confirming it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from leaguepage.config import League
from leaguepage.editorial import load_managers
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names, sleeper_team_names

BLOCKER = "blocker"
WARNING = "warning"

# "Team Name (Callsign)" or "Team Name (A/B)" for a co-managed roster. The
# callsign lives inside the public name by convention; there is no column
# for it, which is exactly why it drifts.
_CALLSIGN_RE = re.compile(r"\(([^)]+)\)\s*$")


@dataclass(frozen=True)
class Finding:
    league: str
    roster_id: int | None
    severity: str
    code: str
    detail: str

    def as_dict(self) -> dict:
        return {"league": self.league, "roster_id": self.roster_id,
                "severity": self.severity, "code": self.code, "detail": self.detail}


def callsigns_in(public_name: str | None) -> list[str]:
    """The callsigns a public name declares, in order.

    `Wild SeeKats (Seebass/Kats)` declares two. A name with no parenthetical
    declares none, which is not a fault -- several teams are just a name.
    """
    if not public_name:
        return []
    m = _CALLSIGN_RE.search(public_name)
    if not m:
        return []
    return [part.strip() for part in m.group(1).split("/") if part.strip()]


def audit_league(storage: Storage, league: League,
                 managers: dict | None = None) -> list[Finding]:
    """Every identity disagreement this league can currently be shown."""
    managers = load_managers() if managers is None else managers
    out: list[Finding] = []
    rosters = storage.get_rosters(league.league_id)
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    resolved = resolve_public_names(storage, league)
    sleeper_names = sleeper_team_names(storage, league)

    # roster_id -> the Sleeper user ids that own it right now
    owners_by_roster: dict[int, set[str]] = {}
    for r in rosters:
        rid = r["roster_id"]
        ids = {i for i in [r.get("owner_id"), *(r.get("co_owners") or [])] if i}
        owners_by_roster[rid] = ids
        if not ids:
            out.append(Finding(league.slug, rid, BLOCKER, "roster-unowned",
                               "Roster has no owner_id and no co-owners in the "
                               "synced Sleeper payload."))
        for uid in ids:
            if uid not in users:
                out.append(Finding(
                    league.slug, rid, BLOCKER, "owner-not-in-league",
                    f"Roster is owned by a Sleeper user id that is not in this "
                    f"league's synced user list ({uid[:6]}…). The roster may "
                    f"have changed hands since the last sync."))

    # What managers.json believes about this league.
    claimed: dict[int, list[str]] = {}
    for key, m in (managers or {}).items():
        binding = ((m.get("leagues") or {}).get(league.slug) or {})
        rid = binding.get("roster_id")
        if rid is None:
            continue
        claimed.setdefault(int(rid), []).append(key)
        uid = m.get("sleeper_user_id")
        if uid and uid not in owners_by_roster.get(int(rid), set()):
            out.append(Finding(
                league.slug, int(rid), BLOCKER, "manager-roster-mismatch",
                f"Editorial metadata binds manager '{key}' to roster {rid}, but "
                f"that user does not own roster {rid} on Sleeper right now."))

    # A roster claimed by more managers than actually own it.
    for rid, keys in claimed.items():
        actual = len(owners_by_roster.get(rid, set()))
        if actual and len(keys) > actual:
            out.append(Finding(
                league.slug, rid, WARNING, "roster-over-claimed",
                f"{len(keys)} managers claim roster {rid} in editorial metadata "
                f"but Sleeper reports {actual} owner(s): {', '.join(sorted(keys))}."))

    # One Sleeper user appearing under two manager keys in the same league.
    by_uid: dict[str, list[str]] = {}
    for key, m in (managers or {}).items():
        if (m.get("leagues") or {}).get(league.slug) and m.get("sleeper_user_id"):
            by_uid.setdefault(m["sleeper_user_id"], []).append(key)
    for uid, keys in by_uid.items():
        if len(keys) > 1:
            out.append(Finding(
                league.slug, None, BLOCKER, "duplicate-manager",
                f"One Sleeper user is recorded under {len(keys)} manager keys: "
                f"{', '.join(sorted(keys))}."))

    # Public names: missing, duplicated, or a callsign used on two rosters.
    seen_names: dict[str, int] = {}
    seen_callsigns: dict[str, int] = {}
    for rid, v in resolved.items():
        name = v.get("name")
        if not name:
            out.append(Finding(league.slug, rid, BLOCKER, "no-public-name",
                               "Roster has no confirmed public display name, so "
                               "it cannot publish."))
            continue
        if name in seen_names:
            out.append(Finding(
                league.slug, rid, BLOCKER, "duplicate-public-name",
                f"Rosters {seen_names[name]} and {rid} publish under the same "
                f"name, which collides their URLs and their team pages."))
        seen_names[name] = rid
        for cs in callsigns_in(name):
            low = cs.lower()
            if low in seen_callsigns and seen_callsigns[low] != rid:
                out.append(Finding(
                    league.slug, rid, WARNING, "callsign-on-two-rosters",
                    f"Callsign '{cs}' appears in the public name of rosters "
                    f"{seen_callsigns[low]} and {rid}."))
            seen_callsigns.setdefault(low, rid)
        sleeper = sleeper_names.get(rid)
        if sleeper and v.get("source") == "commissioner" and sleeper not in name:
            out.append(Finding(
                league.slug, rid, WARNING, "renamed-on-sleeper",
                f"The manager's Sleeper team name is now '{sleeper}', which the "
                f"confirmed public name no longer contains."))

    # Every roster the league has should be described somewhere.
    for r in rosters:
        rid = r["roster_id"]
        if rid not in claimed:
            out.append(Finding(
                league.slug, rid, WARNING, "roster-unclaimed",
                f"No manager record binds roster {rid}, so callbacks, coalitions "
                f"and archive resolution have nothing to key on for it."))
    return out


def audit(storage: Storage, leagues: list[League],
          managers: dict | None = None) -> list[dict]:
    managers = load_managers() if managers is None else managers
    out: list[Finding] = []
    for lg in leagues:
        out.extend(audit_league(storage, lg, managers))
    order = {BLOCKER: 0, WARNING: 1}
    out.sort(key=lambda f: (order[f.severity], f.league, f.roster_id or 0, f.code))
    return [f.as_dict() for f in out]


def spelling_findings(storage: Storage, leagues: list[League],
                      canonical: str, wrong: str) -> list[dict]:
    """Where a superseded spelling of a callsign is still being published.

    Kept separate from the reconciliation above because it is a different
    kind of claim: not "these stores disagree" but "this store is out of
    date". It reads the public name only. Prose is not touched, and a
    quotation that genuinely contained the old spelling stays as written.
    """
    out = []
    for lg in leagues:
        for rid, v in resolve_public_names(storage, lg).items():
            name = v.get("name") or ""
            if wrong.lower() in name.lower():
                out.append({
                    "league": lg.slug, "roster_id": rid,
                    "severity": WARNING, "code": "superseded-callsign",
                    "detail": (f"Public name still spells the callsign "
                               f"'{wrong}'; the canonical spelling is "
                               f"'{canonical}'."),
                })
    return out
