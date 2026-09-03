"""Story Significance — one shared, interpretable answer to the only question
that matters at triage time: is this actually worth talking about?

The system already produces plenty of true facts. This layer separates an
interesting fact from a newsletter story, and it does it the same way
`matchup_interest` scores matchups: named components that each carry points
and their own evidence, summed and clamped, with the breakdown travelling
alongside the score. Nothing here is a black box and nothing is trained; the
weights below are editorial preferences you can argue with and edit.

Two design choices carry most of the weight:

* **Negative components.** Repetition and triviality subtract. Without them a
  long tail of true-but-boring facts (a one-place standings wobble, a kicker
  swap, a 1% playoff drift) accumulates enough small positives to outrank a
  genuine upset. Penalties are what keep the inbox short.
* **Magnitude is normalized inside its own category** before it is scored, so
  a big transaction and a big playoff swing are comparable without either
  category being hardcoded as automatically important. A trade is not
  important because it is a trade; it is important when it is a big one, it
  costs real money, or it changes a contender.
"""
from __future__ import annotations

# Adjustable editorial weights. Positive components accumulate; the two
# penalties subtract. The total clamps to 0..100.
WEIGHTS = {
    "magnitude": 40,        # scaled by the item's own normalized magnitude
    "consequence": 25,      # standings / playoff stakes attached to the change
    "rarity": 20,           # season high or low, record, first occurrence
    "expectation": 20,      # how far this sits from what was expected
    "history": 15,          # archive callback, rivalry, milestone
    "receipt": 20,          # connects to a tracked take or a draft assessment
    "cost": 15,             # FAAB or trade cost as a share of the budget
    "freshness": 10,        # first appearance since the baseline sync
    # penalties
    "repetition": -30,      # this subject/lane was used recently
    "triviality": -25,      # magnitude sits at or under the materiality floor
}

BANDS = ((80, "Lead story"), (60, "Strong"), (40, "Worth a line"), (0, "Minor"))

# Below this normalized magnitude an item is noise even when it is true.
TRIVIAL_MAGNITUDE = 0.15


def band_for(score: int) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return "Minor"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _component(label: str, points: int, evidence: list[str] | None = None) -> dict:
    return {"label": label, "points": int(points), "evidence": evidence or []}


def score_item(item: dict, ctx: dict | None = None, weights: dict = WEIGHTS) -> dict:
    """{'score', 'band', 'components'} for one change/story item.

    `item` carries the signal fields the categories already compute:
      magnitude    0..1, normalized inside the item's own category (required)
      consequence  0..1, how much standings/playoff position rides on it
      rarity       0..1, season high/low, record, first occurrence
      expectation  0..1, distance from what was projected or ranked
      history      0..1, archive/rivalry/milestone connection
      receipt      0..1, connection to a tracked take or draft assessment
      cost         0..1, transaction cost as a share of the waiver budget
      fresh        bool, first appearance since the baseline sync

    `ctx` may carry `repetition` (0..1) computed from prior decisions, so the
    scorer stays a pure function of its inputs and the caller owns the lookup.
    """
    ctx = ctx or {}
    comps: list[dict] = []
    ev = item.get("evidence") or []

    mag = _clamp01(item.get("magnitude", 0.0))
    comps.append(_component(
        f"magnitude within {item.get('category', 'category')}: "
        f"{item.get('magnitude_label') or f'{mag:.0%} of the scale'}",
        round(weights["magnitude"] * mag), ev))

    for key, label in (
        ("consequence", "standings or playoff consequence"),
        ("rarity", "rare for this league and season"),
        ("expectation", "diverges from what was expected"),
        ("history", "connects to league history"),
        ("receipt", "puts a tracked claim on the record"),
        ("cost", "cost real money or real assets"),
    ):
        v = _clamp01(item.get(key, 0.0))
        if v > 0:
            comps.append(_component(
                f"{label} ({item.get(key + '_label') or f'{v:.0%}'})",
                round(weights[key] * v), ev))

    if item.get("fresh"):
        comps.append(_component("new since the last reviewed sync",
                                weights["freshness"], ev))

    # ---- penalties. These are the reason the inbox stays short.
    rep = _clamp01(ctx.get("repetition", 0.0))
    if rep > 0:
        comps.append(_component(
            f"repetition penalty ({ctx.get('repetition_label') or 'this lane ran recently'})",
            round(weights["repetition"] * rep)))
    if mag <= TRIVIAL_MAGNITUDE:
        # scale the penalty so a genuinely flat item is punished hardest
        share = (TRIVIAL_MAGNITUDE - mag) / TRIVIAL_MAGNITUDE
        comps.append(_component(
            f"below the materiality floor for {item.get('category', 'this category')}",
            round(weights["triviality"] * share)))

    score = max(0, min(100, sum(c["points"] for c in comps)))
    return {"score": score, "band": band_for(score), "components": comps}


def rank(items: list[dict], ctx_for=None, weights: dict = WEIGHTS) -> list[dict]:
    """Score every item and return them ranked, highest first. Ties break on
    category then id so ordering is stable across runs and testable."""
    out = []
    for it in items:
        ctx = ctx_for(it) if ctx_for else {}
        out.append({**it, "significance": score_item(it, ctx, weights)})
    out.sort(key=lambda i: (-i["significance"]["score"],
                            i.get("category", ""), i.get("item_id", "")))
    return out


def explain(item: dict) -> list[str]:
    """The 'why surfaced' lines, strongest first, penalties last. This is what
    the Desk shows under an item and what a brief quotes."""
    sig = item.get("significance") or {}
    comps = sorted(sig.get("components", []), key=lambda c: -c["points"])
    lines = []
    for c in comps:
        if c["points"] == 0:
            continue
        sign = "+" if c["points"] > 0 else ""
        lines.append(f"{sign}{c['points']} {c['label']}")
    return lines


# ------------------------------------------------------------ repetition

def lane_of(item_id: str) -> str:
    """The repetition lane for an item: its KIND plus its SUBJECT.

    This used to be the kind alone ("change:standings"), which meant one
    included standings story last week applied the full penalty to all
    twelve teams this week -- including a different team's five-place
    collapse. What actually gets stale is writing about the same team's
    standings twice, so the subject belongs in the lane.

    Deriving it from the id rather than the item body means the lane can be
    recovered from a stored decision row, where only the id survives.
    """
    parts = [p for p in (item_id or "").split(":") if p]
    if not parts:
        return "item"
    return ":".join(parts[:3])


def kind_of(item_id: str) -> str:
    """The story kind alone. Repeating a kind is a milder sin than repeating
    a subject, so it carries a fraction of the penalty."""
    parts = [p for p in (item_id or "").split(":") if p]
    return ":".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "item")


# How much of the repetition penalty a repeated KIND carries, against a
# repeated subject at full weight.
KIND_REPETITION_SHARE = 0.4


def _decay(weeks_ago: int | None) -> float:
    """A lane that ran last week is fully penalised; the penalty decays and
    is gone after four weeks, so a running gag cools off instead of dying."""
    if weeks_ago is None:
        return 0.0
    if weeks_ago <= 1:
        return 1.0
    if weeks_ago == 2:
        return 0.6
    if weeks_ago <= 4:
        return 0.3
    return 0.0


def repetition_context(item: dict, prior_used: dict[str, int]) -> dict:
    """`prior_used` maps lane -> weeks since that lane was last INCLUDED.

    Both lanes are consulted: the exact subject at full weight, the story
    kind at a fraction of it. Writing about the same team's standings two
    weeks running is repetitive; writing about a different team's is barely
    repetitive at all.
    """
    item_id = item.get("item_id", "")
    lane, kind = lane_of(item_id), kind_of(item_id)
    subject_ago = prior_used.get(lane)
    kind_ago = prior_used.get(kind)
    subject = _decay(subject_ago)
    kind_pen = _decay(kind_ago) * KIND_REPETITION_SHARE
    if subject >= kind_pen and subject:
        when = "last week" if subject_ago <= 1 else f"{subject_ago} weeks ago"
        return {"repetition": subject, "repetition_label": f"{lane} ran {when}"}
    if kind_pen:
        when = "last week" if kind_ago <= 1 else f"{kind_ago} weeks ago"
        return {"repetition": kind_pen,
                "repetition_label": f"another {kind} story ran {when}"}
    return {}
