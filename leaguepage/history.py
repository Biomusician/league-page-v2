"""Live history: the part of this site Sleeper cannot reproduce.

Fifty-five issues of Disco Chat sit in the archive. Leaving them behind an
"Archive" tab is leaving the only real moat unused — so a matchup page can
carry what these two have done to each other before, and the newsletter that
said so at the time.

The bar for shipping a callback is high on purpose, because a bad one is
worse than none:

* **Same league.** League memory stays in its league. The scoping rule lives
  in story_memory.ARCHIVE_SCOPE and is not relaxed here.
* **Provenance or it does not ship.** Every item names the issue it came
  from and links to it.
* **Not everything is a rivalry.** Zero to two items. A first meeting between
  two teams with no history produces nothing, and that is the correct output.
* **No repeats.** Public surfacings are recorded per week; a callback that
  ran recently steps aside for the next one.
* **No private handles.** Archive pages are public verbatim, but a snippet
  lifted OUT of one lands on a page that is not exempt from the handle
  audit, so any snippet carrying a login handle is dropped before it can
  reach the build.
"""
from __future__ import annotations

import json
import re

from leaguepage.config import League
from leaguepage.storage import Storage

MAX_ITEMS = 2
REPEAT_COOLDOWN_WEEKS = 3
MIN_QUOTE_WORDS = 12
MAX_QUOTE_CHARS = 240
_FTS_MARK_RE = re.compile(r"\[([^\]]*)\]")
_WS_RE = re.compile(r"\s+")

# Newsletters are half prose and half scoreboard. These mark the scoreboard
# half: draft-result lists, injury tables, matchup headers. A snippet that
# hits any of them is a table fragment, not a callback.
_LISTING_RE = re.compile(
    r"Round \d+:|Winner:|Loser:|\bINJ:|BYE/|\bNSTR\b|\bx\d\)|\d+/\d+\)|"
    r"\bWR\d|\bRB\d|\bQB\d|\bTE\d", re.I)
_FUNCTION_WORDS = {"the", "a", "an", "and", "but", "that", "this", "is", "was",
                   "are", "were", "to", "of", "for", "with", "his", "her",
                   "their", "he", "she", "they", "it", "in", "on", "has",
                   "have", "had", "not", "who", "which", "if", "so", "you"}


def _clean_snippet(snippet: str) -> str:
    """FTS returns match markers and collapsed ellipses; readers want prose."""
    text = _FTS_MARK_RE.sub(r"\1", snippet or "")
    return _WS_RE.sub(" ", text).strip(" …")


def reads_as_prose(text: str) -> bool:
    """Is this a sentence somebody wrote, or a row out of a table?

    Coverage is not the goal. A callback that turns out to be
    "Geronimo Allison, WR88 (Babe x3) Round 14: Winner: Mark Andrews" is
    worse than showing nothing, because it teaches a reader that the
    history section is noise."""
    if not text or _LISTING_RE.search(text):
        return False
    words = text.split()
    if len(words) < MIN_QUOTE_WORDS:
        return False
    # a quote starting with a bare number is the tail of the list above it
    if words[0].strip(".,;:").replace("-", "").isdigit():
        return False
    lowered = [w.strip(".,;:!?'\"()").lower() for w in words]
    if len(_FUNCTION_WORDS.intersection(lowered)) < 3:
        return False
    numeric = sum(1 for w in words if any(ch.isdigit() for ch in w))
    return numeric / len(words) < 0.25


def archive_quote(storage: Storage, issue_id: int, terms: list[str]) -> str | None:
    """The best complete sentence in an archived issue mentioning a term.

    The FTS snippet is a fixed-width window that starts and ends mid-word
    ("...and that could be the deciding"). Going back to the body and taking
    the whole sentence is the difference between a quote and a fragment."""
    from leaguepage.receipts import _sentences

    issue = storage.get_archive_issue(issue_id) or {}
    body = issue.get("body") or ""
    if not body:
        return None
    patterns = [re.compile(rf"\b{re.escape(t)}\b", re.I) for t in terms if t]
    best = None
    for sentence in _sentences(body):
        if len(sentence) > MAX_QUOTE_CHARS or not reads_as_prose(sentence):
            continue
        hits = sum(1 for p in patterns if p.search(sentence))
        if not hits:
            continue
        score = (hits, len(sentence.split()))
        if best is None or score > best[0]:
            best = (score, sentence)
    return best[1] if best else None


def _shown_key(league_slug: str, season: str) -> str:
    return f"history_shown:{league_slug}:{season}"


def shown_weeks(storage: Storage, league_slug: str, season: str) -> dict[str, list[int]]:
    raw = storage.get_meta(_shown_key(league_slug, season))
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def record_shown(storage: Storage, league_slug: str, season: str,
                 item_id: str, week: int) -> None:
    log = shown_weeks(storage, league_slug, season)
    weeks = set(log.get(item_id, []))
    weeks.add(int(week))
    log[item_id] = sorted(weeks)
    storage.set_meta(_shown_key(league_slug, season), json.dumps(log))


def _recently_shown(log: dict[str, list[int]], item_id: str, week: int) -> bool:
    return any(week - w < REPEAT_COOLDOWN_WEEKS and w != week
               for w in log.get(item_id, []))


def matchup_history(storage: Storage, league: League, season: str, week: int,
                    matchup: dict, story_memory: dict, names: dict[int, str],
                    *, private_handles: list[str] | None = None,
                    record: bool = True) -> list[dict]:
    """Zero to two history items for one matchup, strongest first."""
    a, b = matchup["teams"]
    rid_a, rid_b = a["roster_id"], b["roster_id"]
    name_a = names.get(rid_a, f"Roster {rid_a}")
    name_b = names.get(rid_b, f"Roster {rid_b}")
    handles = private_handles or []
    log = shown_weeks(storage, league.slug, season)

    candidates: list[dict] = []

    h2h = matchup.get("h2h") or {}
    rec = h2h.get("record") or {}
    meetings = h2h.get("meetings") or []
    if meetings:
        wa, wb = rec.get(rid_a, 0), rec.get(rid_b, 0)
        if wa == wb:
            headline = f"{wa}–{wb} in this league. Nobody has settled it."
        else:
            lead_name, lead_wins, trail_name, trail_wins = (
                (name_a, wa, name_b, wb) if wa > wb else (name_b, wb, name_a, wa))
            headline = (f"{lead_name} leads {lead_wins}–{trail_wins} "
                        f"over {trail_name}.")
        candidates.append({
            "item_id": f"h2h:{rid_a}:{rid_b}",
            "kind": "Head to head",
            "text": headline,
            "source": f"{len(meetings)} meeting"
                      f"{'' if len(meetings) == 1 else 's'} in the synced season",
            "href": None,
            "weight": 70,
        })
        last = h2h.get("last_meeting")
        if last:
            pa, pb = last["points"].get(rid_a, 0), last["points"].get(rid_b, 0)
            winner = name_a if pa > pb else (name_b if pb > pa else None)
            candidates.append({
                "item_id": f"last:{rid_a}:{rid_b}:{last['week']}",
                "kind": "Last meeting",
                "text": (f"Week {last['week']}: {pa:g} – {pb:g}"
                         + (f", {winner}." if winner else ", a tie.")),
                "source": "this season's results",
                "href": None,
                "weight": 66,
            })

    callbacks = (story_memory or {}).get("callbacks", [])
    terms = [cb.get("matched_term") for cb in callbacks if cb.get("matched_term")]
    for cb in callbacks:
        if cb.get("date_unreliable"):
            continue        # an undated callback cannot carry "back in 2019"
        quote = archive_quote(storage, cb["issue_id"],
                              [cb.get("matched_term"), name_a, name_b])
        if quote is None:
            quote = _clean_snippet(cb.get("snippet") or "")
            if not reads_as_prose(quote):
                continue    # a table fragment is not a callback
        if any(h in quote for h in handles):
            continue        # archive pages are verbatim-public; this page is not
        when = " ".join(str(x) for x in (cb.get("season"), cb.get("week")) if x)
        both = sum(1 for nm in (name_a, name_b)
                   if re.search(rf"\b{re.escape(nm.split(' (')[0])}\b", quote, re.I))
        candidates.append({
            "item_id": f"archive:{cb['issue_id']}",
            "kind": "From the archive",
            "text": f"“{quote}”",
            "source": f"{cb.get('title') or 'archive issue'}"
                      + (f" · {when}" if when else ""),
            "href": f"archive/a{cb['issue_id']}/index.html",
            # a line naming both sides is a rivalry note; one side is colour
            "weight": (78 if cb.get("strength") == "strong" else 60) + 12 * both,
        })

    # Facts may repeat; callbacks may not. "Team 1 leads 2–1" is the current
    # state of the rivalry and belongs on the page every week. A quote from
    # 2019 read twice in a row stops being a callback and starts being
    # furniture, so a recently-used one is dropped outright rather than
    # merely demoted.
    candidates = [c for c in candidates
                  if not (c["href"] and _recently_shown(log, c["item_id"], week))]
    candidates.sort(key=lambda c: (_recently_shown(log, c["item_id"], week),
                                   -c["weight"]))
    chosen = candidates[:MAX_ITEMS]
    if record:
        for c in chosen:
            record_shown(storage, league.slug, season, c["item_id"], week)
    return chosen
