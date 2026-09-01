"""The All-City Team — a reusable editorial sidebar feature.

Premise: the best fantasy starting lineup buildable out of players whose first
or last name is exactly the name of a real city.

One edition per run lives in `editorial/features/<feature>/<edition>.json`,
git-tracked and hand-edited the same way `editorial/coalitions.json` is. A
rerun later in the season is a NEW edition file bound to a new issue_key, so
an edition that already published stays frozen with the issue that carried it
and the published snapshot never has to be touched.

The feature key is the module key, so a rules variant is a new directory plus
one MODULE_DEFS line. Two ship today: `all-city` (any incorporated city) and
`all-city-marquee` (the same exercise with a 100,000 population floor, set by
`rules.minimum_population`).

This module owns the structured half of the feature: it validates an edition,
mechanically re-checks the exact-match rule against the player's own name, and
renders the roster table, the rule footnote and the near-miss list. The prose
around it is ordinary commissioner-owned section copy in `sections/all-city.md`.

Only PUBLIC_ENTRY_FIELDS ever reach rendered output. Source URLs, evidence IDs
and research notes stay in the local data file for pre-publication review.
"""
from __future__ import annotations

import json
from pathlib import Path

from leaguepage.config import EDITORIAL_DIR

FEATURE_KEY = "all-city"          # the default/original variant
FEATURE_TITLE = "The All-City Team"

# Slots the feature knows how to render. FLEX/DST/bench are deliberately
# absent: adding them is a data change plus one line here, not a redesign.
KNOWN_POSITIONS = ("QB", "RB", "WR", "TE", "K")
DEFAULT_ROSTER_FORMAT = (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1))

# Qualification tiers. The tier is editorial colour; the pass/fail decision is
# `municipal_class == "city"`, which is a legal classification, not a judgment.
# The tier itself is mechanical too, so nobody has to argue about whether a
# given place "feels" marquee: it is the census count and nothing else.
QUALIFICATIONS = {
    "marquee": "Marquee City",
    "city": "City",
    "technical": "Technical Qualifier",
}
MARQUEE_FLOOR = 100_000
TECHNICAL_CEILING = 5_000

VERDICTS = ("Elite", "Strong starter", "Viable starter", "Weak link",
            "Technicality doing a lot of work here")

# The premise allows these four countries; the default rule is the U.S. one.
COUNTRIES = ("United States", "France", "United Kingdom", "Sweden")

NAME_PARTS = ("first", "last")

# Table columns are data-driven so a variant can show what actually varies in
# it. The marquee edition is every-row-marquee, so it prints population instead.
COLUMNS = {
    "pos": "POS", "player": "PLAYER", "city": "CITY", "class": "CLASS",
    "population": "POPULATION", "verdict": "FANTASY VERDICT",
}
DEFAULT_COLUMNS = ("pos", "player", "city", "class", "verdict")

# Everything a reader may see. The renderer reads nothing else, so a private
# field added to the data later cannot leak by being forgotten about.
PUBLIC_ENTRY_FIELDS = frozenset({
    "position", "slot", "player", "nfl_team", "matching_name", "name_part",
    "city", "state", "country", "municipal_class", "population",
    "population_year", "qualification", "verdict", "assessment", "exception",
})
PRIVATE_ENTRY_FIELDS = frozenset({"evidence", "sources", "research_notes",
                                  "consensus"})


def tier_for_population(population: int) -> str:
    if population >= MARQUEE_FLOOR:
        return "marquee"
    if population < TECHNICAL_CEILING:
        return "technical"
    return "city"


def features_dir(base_dir: Path | None = None, feature_key: str = FEATURE_KEY) -> Path:
    """Edition directory. `base_dir` is the editorial root, matching the
    base_dir the issue builder threads through everywhere else; `feature_key`
    is the module key, so each rules variant gets its own directory."""
    return (base_dir or EDITORIAL_DIR) / "features" / feature_key


def load_edition(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_editions(base_dir: Path | None = None,
                  feature_key: str = FEATURE_KEY) -> list[dict]:
    d = features_dir(base_dir, feature_key)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(load_edition(p))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue  # validate_edition reports it; a broken file never renders
        out[-1]["_path"] = p.as_posix()
    return out


def find_edition(season: str, issue_key: str, league_slug: str,
                 *, base_dir: Path | None = None,
                 feature_key: str = FEATURE_KEY) -> dict | None:
    """The edition bound to exactly this issue, or None. Binding is explicit:
    an edition names its season and issue_key, and optionally the leagues it
    runs in. No 'latest wins' fallback, so a published issue can never pick up
    a different edition on a later rebuild."""
    for ed in list_editions(base_dir, feature_key):
        if str(ed.get("season")) != str(season) or ed.get("issue_key") != issue_key:
            continue
        leagues = ed.get("leagues")
        if leagues and league_slug not in leagues:
            continue
        return ed
    return None


def _name_tokens(full_name: str) -> tuple[str, str]:
    """(first, last) with suffixes and punctuation stripped. Suffixes are not
    names, so 'Travis Etienne Jr.' is (Travis, Etienne)."""
    parts = [p.strip(".,") for p in full_name.split()]
    parts = [p for p in parts if p and p.lower().rstrip(".") not in
             ("jr", "sr", "ii", "iii", "iv", "v")]
    if not parts:
        return "", ""
    return parts[0], parts[-1]


def roster_format(edition: dict) -> list[tuple[str, int]]:
    raw = edition.get("roster_format")
    if not raw:
        return [tuple(x) for x in DEFAULT_ROSTER_FORMAT]
    return [(str(pos), int(count)) for pos, count in raw]


def columns_for(edition: dict) -> list[str]:
    return [str(c) for c in (edition.get("columns") or DEFAULT_COLUMNS)]


def minimum_population(edition: dict) -> int | None:
    mp = (edition.get("rules") or {}).get("minimum_population")
    return int(mp) if mp else None


def validate_edition(edition: dict) -> list[str]:
    """Everything that must hold before an edition can render. Returns human
    readable problems; empty list means the edition is publishable."""
    errors: list[str] = []
    for field in ("edition", "season", "issue_key", "title", "compiled_at", "rules"):
        if not edition.get(field):
            errors.append(f"missing required field '{field}'")
    rules = edition.get("rules") or {}
    if isinstance(rules, dict) and not rules.get("public_summary"):
        errors.append("rules.public_summary is required (it prints under the table)")

    try:
        fmt = roster_format(edition)
    except (TypeError, ValueError):
        errors.append("roster_format is malformed")
        fmt = []
    for pos, count in fmt:
        if pos not in KNOWN_POSITIONS:
            errors.append(f"roster_format names unknown position '{pos}'")
        if count < 1:
            errors.append(f"roster_format asks for {count} {pos} slots")

    cols = columns_for(edition)
    for c in cols:
        if c not in COLUMNS:
            errors.append(f"unknown table column '{c}'")
    for required in ("pos", "player", "city"):
        if required not in cols:
            errors.append(f"table columns must include '{required}'")

    floor = minimum_population(edition)

    starters = edition.get("starters") or []
    if not isinstance(starters, list):
        return errors + ["starters must be a list"]

    seen_slots: set[tuple[str, int]] = set()
    seen_players: set[str] = set()
    counts: dict[str, int] = {}
    for i, e in enumerate(starters):
        where = f"starter {i + 1}"
        if not isinstance(e, dict):
            errors.append(f"{where} is not an object")
            continue
        player = e.get("player") or ""
        where = f"starter {i + 1} ({player or 'unnamed'})"
        for field in ("position", "slot", "player", "matching_name", "name_part",
                      "city", "country", "municipal_class",
                      "qualification", "verdict", "assessment"):
            if not e.get(field):
                errors.append(f"{where}: missing '{field}'")
        # State is the U.S. locator; an allied city is located by its country.
        if e.get("country") == "United States" and not e.get("state"):
            errors.append(f"{where}: missing 'state'")
        if floor is not None:
            pop = e.get("population")
            if not isinstance(pop, int):
                errors.append(f"{where}: this edition has a {floor:,} population "
                              "floor, so the population must be recorded")
            elif pop < floor:
                errors.append(f"{where}: {e.get('city')} is {pop:,}, below this "
                              f"edition's {floor:,} floor")
        if "population" in cols and not isinstance(e.get("population"), int):
            errors.append(f"{where}: the table prints population, so it must be recorded")
        pos = e.get("position")
        if pos not in KNOWN_POSITIONS:
            errors.append(f"{where}: unknown position '{pos}'")
        else:
            counts[pos] = counts.get(pos, 0) + 1
        slot = e.get("slot")
        if isinstance(slot, int):
            key = (str(pos), slot)
            if key in seen_slots:
                errors.append(f"{where}: duplicate {pos} slot {slot}")
            seen_slots.add(key)
        else:
            errors.append(f"{where}: slot must be an integer")
        if player:
            if player in seen_players:
                errors.append(f"{where}: appears twice in the lineup")
            seen_players.add(player)

        # The rule itself, enforced mechanically rather than trusted: the
        # matching name must BE the player's first or last name, exactly, and
        # the city must carry exactly that name.
        part = e.get("name_part")
        match = e.get("matching_name") or ""
        if part not in NAME_PARTS:
            errors.append(f"{where}: name_part must be 'first' or 'last'")
        elif player:
            first, last = _name_tokens(player)
            actual = first if part == "first" else last
            if actual != match:
                errors.append(
                    f"{where}: {part} name is '{actual}', which does not equal "
                    f"matching_name '{match}'")
        if match and e.get("city") and e["city"] != match:
            errors.append(f"{where}: city '{e['city']}' is not an exact match "
                          f"for '{match}'")
        if e.get("municipal_class") and e["municipal_class"] != "city":
            errors.append(f"{where}: municipal_class is "
                          f"'{e['municipal_class']}'; only a city qualifies")
        if e.get("country") and e["country"] not in COUNTRIES:
            errors.append(f"{where}: country '{e['country']}' is outside the rule")
        if e.get("qualification") and e["qualification"] not in QUALIFICATIONS:
            errors.append(f"{where}: unknown qualification '{e['qualification']}'")
        elif isinstance(e.get("population"), int):
            expected = tier_for_population(e["population"])
            if e.get("qualification") != expected:
                errors.append(
                    f"{where}: population {e['population']:,} makes this a "
                    f"'{expected}', not a '{e.get('qualification')}'")
        if e.get("verdict") and e["verdict"] not in VERDICTS:
            errors.append(f"{where}: unknown verdict '{e['verdict']}'")
        if not e.get("evidence"):
            errors.append(f"{where}: no evidence reference")
        if not e.get("sources"):
            errors.append(f"{where}: no source for the city claim")

    for pos, count in fmt:
        have = counts.get(pos, 0)
        if have != count:
            errors.append(f"lineup has {have} {pos}, the format asks for {count}")
    extra = set(counts) - {pos for pos, _ in fmt}
    for pos in sorted(extra):
        errors.append(f"lineup has {counts[pos]} {pos}, which the format has no slot for")

    for i, nm in enumerate(edition.get("near_misses") or []):
        if not isinstance(nm, dict):
            errors.append(f"near miss {i + 1} is not an object")
            continue
        for field in ("player", "position", "reason"):
            if not nm.get(field):
                errors.append(f"near miss {i + 1}: missing '{field}'")
    # The bench is the reusable research record and never renders, so it gets
    # a lighter check: enough structure that a rerun can trust it.
    for i, b in enumerate(edition.get("bench") or []):
        if not isinstance(b, dict):
            errors.append(f"bench entry {i + 1} is not an object")
            continue
        for field in ("player", "position", "city", "note"):
            if not b.get(field):
                errors.append(f"bench entry {i + 1}: missing '{field}'")
    return errors


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k in PUBLIC_ENTRY_FIELDS}


def _ordered_starters(edition: dict) -> list[dict]:
    order = {pos: i for i, (pos, _) in enumerate(roster_format(edition))}
    return sorted(edition.get("starters") or [],
                  key=lambda e: (order.get(e.get("position"), 99), e.get("slot", 0)))


def _city_label(e: dict) -> str:
    bits = [e["city"]]
    if e.get("state"):
        bits.append(e["state"])
    label = ", ".join(bits)
    if e.get("country") and e["country"] != "United States":
        label += f" ({e['country']})"
    return label


def _exception_mark(index: int) -> str:
    """Named carve-outs from the rule get a numbered mark against the city and
    a footnote under the table. A ruling the reader cannot see is not a rule."""
    return f"[{index}]"


def _cell(e: dict, column: str, marks: dict[str, int] | None = None) -> str:
    if column == "pos":
        return e["position"]
    if column == "player":
        return e["player"] + (f" ({e['nfl_team']})" if e.get("nfl_team") else "")
    if column == "city":
        label = _city_label(e)
        idx = (marks or {}).get(e.get("exception") or "")
        return f"{label} {_exception_mark(idx)}" if idx else label
    if column == "class":
        return QUALIFICATIONS.get(e["qualification"], e["qualification"])
    if column == "population":
        pop = e.get("population")
        return f"{pop:,}" if isinstance(pop, int) else ""
    if column == "verdict":
        return e["verdict"]
    return ""


def exception_notes(edition: dict) -> list[str]:
    """Distinct exception texts, in lineup order. Each is a named, deliberate
    carve-out from the default rule, so it prints rather than hiding in data."""
    out: list[str] = []
    for e in _ordered_starters(edition):
        note = e.get("exception")
        if note and note not in out:
            out.append(note)
    return out


def source_line(edition: dict) -> str:
    """Public provenance: source labels and retrieval dates, no URLs and no
    evidence IDs, matching how ADP provenance already prints on the site."""
    labels = []
    for s in edition.get("sources") or []:
        label = s.get("label")
        if not label:
            continue
        when = (s.get("retrieved") or "")[:10]
        labels.append(f"{label} ({when})" if when else label)
    return "; ".join(labels)


def render_markdown(edition: dict) -> str:
    """Table, rule footnote and near-miss list. The surrounding prose is the
    commissioner's section copy and is spliced in by the issue builder."""
    cols = columns_for(edition)
    notes = exception_notes(edition)
    marks = {note: i + 1 for i, note in enumerate(notes)}
    lines = [
        "| " + " | ".join(COLUMNS[c] for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for raw in _ordered_starters(edition):
        e = _public(raw)
        lines.append("| " + " | ".join(_cell(e, c, marks) for c in cols) + " |")
    rules = edition.get("rules") or {}
    lines += ["", f"*{rules['public_summary']}*"]
    for i, note in enumerate(notes, start=1):
        lines += ["", f"*{_exception_mark(i)} {note}*"]
    foot = []
    src = source_line(edition)
    if src:
        foot.append(f"Valuation from {src}.")
    if rules.get("verification_note"):
        foot.append(rules["verification_note"])
    if foot:
        lines += ["", f"*{' '.join(foot)}*"]
    return "\n".join(lines)


def render_near_misses(edition: dict) -> str:
    misses = edition.get("near_misses") or []
    if not misses:
        return ""
    heading = edition.get("near_miss_title") or "Outside the City Limits"
    lines = [f"### {heading}", ""]
    for nm in misses:
        who = nm["player"]
        tag = ", ".join(x for x in (nm.get("position"), nm.get("nfl_team")) if x)
        if tag:
            who += f" ({tag})"
        lines.append(f"- **{who}.** {nm['reason']}")
    return "\n".join(lines)


def render_section(edition: dict, prose: str | None) -> str:
    """Full published section body: table, then the commissioner's prose, then
    the near misses."""
    parts = [render_markdown(edition)]
    if prose and prose.strip():
        parts.append(prose.strip())
    misses = render_near_misses(edition)
    if misses:
        parts.append(misses)
    return "\n\n".join(parts)
