"""Show candidate Takes from published issues. Creates nothing.

    scripts/take_candidates.py                 # both leagues, every issue
    scripts/take_candidates.py --league disco

The scan is tuned for precision, not recall: three candidates worth tracking
beat twenty that have to be waded through. Everything it finds is offered,
never created — the Commissioner tracks a take on the Desk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from leaguepage import takes
from leaguepage.config import LEAGUES, PUBLISHED_DIR
from leaguepage.site_build import _load_snapshots, _team_slugs
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names


def context_for(storage: Storage, league) -> dict:
    from leaguepage.pubqa import _norm_tokens

    names = resolve_public_names(storage, league)
    public = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    players: dict[str, str] = {}
    for r in storage.get_rosters(league.league_id):
        for pid in (r.get("players") or []):
            p = storage.get_player(pid) or {}
            if p.get("full_name"):
                players.setdefault(p["full_name"], (p.get("position") or "").upper())
    drafts = storage.get_drafts_for_league(league.league_id)
    if drafts:
        for p in storage.get_draft_picks(drafts[0]["draft_id"]):
            meta = p.get("metadata") or {}
            nm = " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x).strip()
            if nm:
                players.setdefault(nm, (meta.get("position") or "").upper())
    return {
        "name_tokens": {rid: _norm_tokens(nm) for rid, nm in public.items()},
        "public_names": public,
        "slugs": _team_slugs(storage, league, names),
        "player_positions": players,
        "author_roster_id": league.author_roster_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league")
    ap.add_argument("--issue")
    args = ap.parse_args()

    total = 0
    for league in [l for l in LEAGUES if not args.league or l.slug == args.league]:
        with Storage() as s:
            ctx = context_for(s, league)
            season = str((s.get_league(league.league_id) or {}).get("season") or "")
            tracked = {t["quote"] for t in s.all_takes(league.slug, season)}
        for snap in _load_snapshots(league, PUBLISHED_DIR):
            if args.issue and snap["issue_key"] != args.issue:
                continue
            cands = takes.candidate_takes(snap, existing_quotes=tracked, **ctx)
            print(f"\n{'='*74}\n{league.slug} · {snap['season']} · "
                  f"{snap['issue_label']} — {len(cands)} candidate(s)\n{'='*74}")
            for i, c in enumerate(cands, 1):
                subj = c.get("subject_name") or "(no single subject)"
                print(f"\n{i}. [{c['topic']}] {subj}   score {c['score']}")
                print(f"   “{c['quote']}”")
                print(f"   why: {'; '.join(c['reasons'])}")
                print(f"   from: {c['section_title']}")
            total += len(cands)
    print(f"\nTOTAL candidates offered: {total}   (none created)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
