"""Deterministic default copy for sections that are assembled from results.

Some sections have an answer before anyone writes a word. Weekly Hardware is
the clearest case: the awards are computed from the week's scoring and the
winners are decided on the Desk, so the facts are settled and the only open
question is how they read.

The Commissioner is entitled to both. This module composes the settled facts
into publishable prose so there is always a default, and the Desk lets him
replace that default with his own writing. Nothing here invents a number:
every line traces to a decided award and its computed basis, and a decision
he has not made produces no copy at all.

Two things stay out of the composed text on purpose:

  * the commissioner note on an award decision, which is his private
    steering for the writing brief and was never meant for a reader; and
  * evidence references, which identify datasets rather than say anything.

Both are still shown on the Desk beside the section, as evidence he can read
while he edits. They are simply not prose.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.storage import Storage

# Sections whose public body is produced by code rather than typed, and for
# which this module can compose a default. The Desk offers "use the
# generated copy" only for these.
GENERATED_DEFAULTS = {"hardware"}


def hardware_evidence(storage: Storage, league: League, season: str,
                      issue_key: str) -> list[dict]:
    """The decided awards behind Weekly Hardware, with their computed basis.

    This is the immutable half of the section: results, not readings. It is
    what the Desk shows under "computed evidence", and it is what the
    generated copy below is composed from. An award he has not decided is
    absent rather than guessed at.
    """
    from leaguepage.desk import awards_for

    decisions = storage.get_award_decisions(league.slug, season, issue_key)
    out = []
    for aw in awards_for(storage, league, season, issue_key):
        d = decisions.get(aw["award_key"])
        if not d or d["decision"] not in ("awarded", "manual"):
            continue
        winner = d.get("winner")
        nominee = next(
            (n for n in aw["nominees"]
             if winner and winner in (n.get("player"), n.get("team_slug"))),
            None)
        out.append({
            "award_key": aw["award_key"],
            "award_name": aw["award_name"],
            "metric": aw["metric"],
            "winner": winner,
            "decision": d["decision"],
            # Private: the Desk shows it, the composed prose does not.
            "note": d.get("note"),
            "facts": list((nominee or {}).get("facts") or []),
            "evidence": list((nominee or {}).get("evidence") or []),
        })
    return out


def compose_hardware(rows: list[dict]) -> str | None:
    """Weekly Hardware composed from decided awards, or None.

    None when nothing has been decided, because a section that says "no
    awards this week" is a claim, and an empty file is the honest way to
    say the Commissioner has not got there yet.

    Split from the lookup above so the Desk can compute the evidence once
    per page and both show it and offer the copy made from it.
    """
    if not rows:
        return None
    parts = []
    for r in rows:
        block = [f"### {r['award_name']}", ""]
        if r["winner"]:
            block.append(f"**{r['winner']}**")
            block.append("")
        block.append(f"{r['metric']}")
        if r["facts"]:
            block.append("")
            block += [f"- {f}" for f in r["facts"]]
        parts.append("\n".join(block))
    return "\n\n".join(parts) + "\n"


def compose(section: str, rows: list[dict]) -> str | None:
    """The composed default for `section`, from evidence already computed.

    The Desk needs the evidence anyway to show it, so this takes the rows
    rather than looking them up again.
    """
    if section == "hardware":
        return compose_hardware(rows)
    return None


def generated_md(storage: Storage, league: League, season: str, issue_key: str,
                 section: str) -> str | None:
    """The composed default for `section`, or None if it has none."""
    return compose(section, evidence_for(storage, league, season, issue_key, section))


def evidence_for(storage: Storage, league: League, season: str, issue_key: str,
                 section: str) -> list[dict]:
    """The computed results behind `section`, for the Desk to display."""
    if section == "hardware":
        return hardware_evidence(storage, league, season, issue_key)
    return []


# The provenance method key that describes where the composed copy came
# from. Kept here so the caller cannot pass a string the allowlist rejects.
GENERATED_METHOD = {"hardware": "weekly-awards"}
