"""Your Team This Week — the personal briefing at the top of a team page.

A manager arrives with five questions and about twenty seconds: how am I
doing, what changed, what is actually wrong with my roster, what was my
biggest move, and what matters next. The page had all of that material and
made them parse six tables to assemble it.

Two ideas do the work here.

**Analytical rank is not editorial importance.** The positional table ranks
every room including K and DEF, and it should — that is a fact about the
roster. But "K is your second-best strength" is not a headline. Skill-
position construction decides a fantasy season; special teams reach the
briefing only when they earn it (an unusual cost, a lineup problem, a real
weekly result). `editorial_strengths` and `editorial_weaknesses` implement
that split, leaving the analytical table untouched.

**Section priority moves with the season.** In August the Draft Recap is the
story. By week eight it is context, and this week's performance outranks
draft-night analysis. `section_order` reorders rather than deletes: the
draft data never goes away, it just stops being first.
"""
from __future__ import annotations

from leaguepage.draft_value import SKILL_POSITIONS, SPECIAL_TEAMS
from leaguepage.front_page import (
    MIDSEASON, OPENING, PLAYOFF_RACE, POSTSEASON, PRESEASON,
)

# Where a special-teams room may still earn a headline slot.
ST_HEADLINE_RANK = 1        # league-best, and even then only as a footnote


def editorial_strengths(profile: dict, rid: int, sw: dict) -> tuple[list[str], list[str]]:
    """(strengths, weaknesses) reordered by editorial importance.

    Same underlying facts as strengths_weaknesses; skill positions simply
    come first. A K or DEF line survives only when it is the league's best
    or worst, and never displaces a skill-position line."""
    def key(entry):
        pos = entry.get("position") or ""
        return (0 if pos in SKILL_POSITIONS else 1, entry.get("rank") or 99)

    def keep(entries, *, worst: bool):
        skill = [e for e in entries if (e.get("position") or "") in SKILL_POSITIONS]
        st = [e for e in entries if (e.get("position") or "") in SPECIAL_TEAMS]
        n = profile["n"] if profile else 0
        st = [e for e in st
              if (e.get("rank") == ST_HEADLINE_RANK and not worst)
              or (worst and e.get("rank") == n)]
        return sorted(skill, key=key) + st

    return ([e["note"] for e in keep(sw["strengths"], worst=False)],
            [e["note"] for e in keep(sw["weaknesses"], worst=True)])


def _best_and_worst(profile: dict, rid: int) -> tuple[dict | None, dict | None]:
    if not profile:
        return None, None
    skill = [p for p in profile["positions"] if p in SKILL_POSITIONS]
    if not skill:
        return None, None
    best = min(skill, key=lambda p: profile["ranks"][p][rid])
    worst = max(skill, key=lambda p: profile["ranks"][p][rid])
    room = profile["teams"][rid]

    def entry(pos):
        t = room[pos]
        nuance = None
        if t["fragility"] >= 0.6 and t["count"] > 1:
            nuance = f"{int(t['fragility'] * 100)}% of it is {t['top_player']}"
        elif (profile["starter_ranks"][pos][rid] <= round(0.4 * profile["n"])
              and profile["depth_ranks"][pos][rid] >= round(0.8 * profile["n"])):
            nuance = "the starters hold up; there is nothing behind them"
        return {"pos": pos, "rank": profile["ranks"][pos][rid],
                "n": profile["n"], "nuance": nuance}

    return entry(best), entry(worst)


def storyline(*, state: str, record: dict, form: dict | None, streak: dict | None,
              all_play: dict | None, best: dict | None, worst: dict | None,
              key_move: dict | None, weeks_played: int) -> str | None:
    """One line for what this season is about so far.

    Derived from signals that exist, deliberately hedged. "Strong roster, bad
    luck" is a real thing to say when all-play and record disagree; it is not
    a thing to say from two games."""
    if state == PRESEASON:
        if best and worst:
            return (f"Built around {best['pos']}, exposed at {worst['pos']} — "
                    "the season will decide whether that trade is the right one.")
        return None
    wins, losses = record.get("wins", 0), record.get("losses", 0)
    if all_play and weeks_played >= 3:
        ap_rate = all_play["wins"] / max(1, all_play["wins"] + all_play["losses"])
        real_rate = wins / max(1, wins + losses)
        if ap_rate - real_rate >= 0.25:
            return ("Strong roster, bad schedule: the all-play record is well "
                    "ahead of the real one.")
        if real_rate - ap_rate >= 0.25:
            return ("The record is ahead of the performance — this team has "
                    "been winning the weeks it needed to.")
    if streak and streak.get("length", 0) >= 3:
        return (f"{streak['length']} straight weeks of {streak['kind']}, and "
                "the table has started to notice.")
    if wins + losses >= 4 and losses >= 2 and wins >= 3:
        return f"{wins}-{losses} after a slow start."
    if worst and worst["rank"] >= round(0.8 * (worst["n"] or 1)):
        return (f"{worst['pos']} is still the problem, and it has not been "
                "solved from the wire yet.")
    if key_move:
        return "The roster has been actively worked; the wire is doing real work here."
    return None


def build(*, state: str, name: str, record: dict, standing: int | None,
          weeks_played: int, profile: dict | None, rid: int, form: dict | None,
          streak: dict | None, all_play: dict | None, playoff_line: str | None,
          playoff_delta: str | None, key_moves: list[dict], next_matchup: dict | None,
          deltas: list[str], receipts: list[dict]) -> dict:
    """The briefing block. Every field is optional; the template renders
    whatever is real and nothing else."""
    best, worst = _best_and_worst(profile, rid)
    key_move = key_moves[-1] if key_moves else None

    position = None
    if weeks_played:
        position = f"{record.get('wins', 0)}-{record.get('losses', 0)}"
        if standing:
            position = f"{_ordinal(standing)} · {position}"
    elif standing and profile:
        position = f"Preseason · model board {_ordinal(standing)} of {profile['n']}"

    form_line = None
    if form:
        form_line = f"#{form['rank']} scoring over the last {form['window_label']}"
        if streak:
            form_line += f" · {streak['length']} straight weeks of {streak['kind']}"
    elif streak:
        form_line = f"{streak['length']} straight weeks of {streak['kind']}"

    move_line = None
    if key_move:
        move_line = {"line": key_move["line"],
                     "detail": key_move.get("text"),
                     "shift": key_move.get("rank_shift"),
                     "questionable": key_move.get("questionable")}

    watch: list[str] = []
    if worst and worst["rank"] >= round(0.7 * worst["n"]):
        watch.append(f"{worst['pos']} is the room most likely to cost a week"
                     + (f" — {worst['nuance']}" if worst["nuance"] else ""))
    if best and best.get("nuance") and "nothing behind" in (best["nuance"] or ""):
        watch.append(f"{best['pos']} carries this roster and has no cover behind it")
    for r in receipts[:1]:
        watch.append(f"An earlier claim about this team is {r['status'].lower()}.")

    return {
        "state": state,
        "position": position,
        "playoff": playoff_line,
        "playoff_delta": playoff_delta,
        "form": form_line,
        "strength": (f"{best['pos']} · #{best['rank']} of {best['n']}"
                     if best else None),
        "strength_note": best["nuance"] if best else None,
        "weakness": (f"{worst['pos']} · #{worst['rank']} of {worst['n']}"
                     if worst else None),
        "weakness_note": worst["nuance"] if worst else None,
        "key_move": move_line,
        "next": next_matchup,
        "changed": deltas[:4],
        "watch": watch[:2],
        "storyline": storyline(state=state, record=record, form=form,
                               streak=streak, all_play=all_play, best=best,
                               worst=worst, key_move=key_move,
                               weeks_played=weeks_played),
        "receipts": receipts[:2],
    }


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ------------------------------------------------------- section ordering

# Section keys in the order they appear for each season state. Nothing is
# ever dropped — the draft simply stops being the headline once there are
# results to lead with.
_ORDERS = {
    PRESEASON: ["positional", "draft", "moves", "performance", "roster",
                "mentions", "awards"],
    OPENING: ["positional", "draft", "moves", "performance", "roster",
              "mentions", "awards"],
    MIDSEASON: ["performance", "positional", "moves", "draft", "roster",
                "mentions", "awards"],
    PLAYOFF_RACE: ["performance", "positional", "moves", "roster", "draft",
                   "mentions", "awards"],
    POSTSEASON: ["performance", "positional", "moves", "roster", "awards",
                 "mentions", "draft"],
}


def section_order(state: str) -> list[str]:
    return list(_ORDERS.get(state, _ORDERS[PRESEASON]))


# ------------------------------------------------------- league mentions


def league_mentions(snaps: list[dict], team_name: str, rid: int,
                    name_tokens: dict[int, set[str]], *, limit: int = 3) -> list[dict]:
    """"Last time we talked about you" — real references, not substring hits.

    The old check was `team_name in section_content`, which matches a name
    inside somebody else's sentence and links to an issue with no quote and
    no reason. This finds the paragraph the team is actually the subject of,
    pulls the sentence, and carries the issue and section it came from."""
    import re

    from leaguepage.pubqa import QAContext, _team_blocks
    from leaguepage.receipts import _distinctive_tokens, _sentences

    all_distinctive = _distinctive_tokens(name_tokens)
    mine = {tok for tok, r in all_distinctive.items() if r == rid}
    theirs = {tok for tok, r in all_distinctive.items() if r != rid}
    ctx = QAContext(league_slug="", season="", issue_key="", n_teams=len(name_tokens))
    ctx.name_tokens = name_tokens
    ctx.public_names = {r: "" for r in name_tokens}
    pattern = (re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(mine))
                          + r")\b", re.I) if mine else None)

    def usable(sentence: str) -> bool:
        # a callback has to read as a sentence somebody wrote, and it has to
        # be about this team rather than mentioning it in passing
        if len(sentence.split()) < 8 or "|" in sentence:
            return False
        return not any(re.search(rf"\b{re.escape(t)}\b", sentence, re.I)
                       for t in theirs)

    out: list[dict] = []
    seen: set[str] = set()

    def add(sentence, snap, section):
        key = sentence[:60]
        if key in seen:
            return
        seen.add(key)
        out.append({
            "quote": sentence.strip(),
            "issue_label": snap["issue_label"],
            "season": snap["season"],
            "section_title": section.get("title") or "",
            "href": snap["href"],
            "anchor": section.get("module_key"),
        })

    for snap in snaps:
        for section in snap.get("sections", []):
            text = section.get("content_md") or ""
            # A per-team block is about this team even when no sentence in it
            # repeats the name — the heading already said so.
            for _heading, body, block_rid in _team_blocks(text, ctx):
                if block_rid != rid:
                    continue
                for sentence in _sentences(body):
                    if len(sentence.split()) >= 8 and "|" not in sentence:
                        add(sentence, snap, section)
                        break
            if pattern:
                for sentence in _sentences(text):
                    if pattern.search(sentence) and usable(sentence):
                        add(sentence, snap, section)
                        break   # one line per section: a callback, not a feed
    return out[:limit]
