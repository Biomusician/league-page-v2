"""One structured brief per section, whoever is going to write from it.

Six different places built context for a writer: the Claude prompt route,
the rewrite-request file, the Lowdown prep, the per-section AUTHORING
files, the matchup packet's authoring brief, and the draft packet's. Each
knew a slightly different set of facts, which meant the answer depended on
which button was pressed, and pointing a second assistant at the work
meant reconstructing the context by hand.

A WritingPacket is that context, once. It is assembled from what the app
already computes, and it is deliberately a plain dataclass of strings and
lists rather than a prompt: how it is delivered — copied for Claude Code,
copied for ChatGPT, handed to a local worker, or one day sent to an API —
is a separate decision, and none of those may change what the facts are.

Two rules hold it together.

**Safety.** `render()` emits only fields on this dataclass, and the
assembler puts nothing in them that a reader could not see: public team
names, synced league facts, the section's own evidence, the Commissioner's
style rules. No filesystem paths, no manager handles, no private notes, no
chain of thought. `redact()` is the one place that decides, so the test
suite has one thing to point at.

**Honesty about providers.** Nothing here assumes a subscription is an
API. A Claude Max or ChatGPT Plus session is a person's account, not a
service this app can call, so the manual handoff is a first-class delivery
mode rather than a placeholder for one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from leaguepage.config import League
from leaguepage.storage import Storage

# How a packet reaches a writer. The first two are what exists today and
# need no credentials; the last two are designed for and not built.
DELIVERY = ("copy-for-claude", "copy-for-chatgpt", "local-worker", "api")

# Where a packet's prose is expected to come from, which the Desk already
# records per section as provenance origin.
AUTHORSHIP = ("commissioner", "ai", "deterministic")

# Sections the Commissioner writes himself as a rule. AI may research
# them; it does not supply the wording. Matchups are the product rule from
# 2026-09-05; the Lowdown has always been his.
COMMISSIONER_AUTHORED = ("lowdown",)

_PATHISH = re.compile(r"[A-Za-z]:[\\/][^\s]*|(?:\.{0,2}/)?(?:[\w.-]+/){2,}[\w.-]+"
                      r"|\b[\w.-]+\.(?:md|json|py|sqlite3|html|log)\b")


@dataclass
class WritingPacket:
    """Everything a writer needs for one section, and nothing else."""

    league: str
    season: str
    issue_key: str
    section: str
    section_title: str
    purpose: str                      # what this section is for, in one line
    authorship: str                   # who supplies the wording, per the rules above
    format_note: str = ""             # "12-team Superflex, half PPR" and the like
    week: int | None = None
    style_rules: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)        # section evidence
    stories: list[str] = field(default_factory=list)      # Command Brief top stories
    continuity: list[str] = field(default_factory=list)   # takes, receipts, callbacks
    constraints: list[str] = field(default_factory=list)  # what the data cannot support
    current_prose: str = ""
    instruction: str = ""             # his rewrite note, when there is one
    provenance_note: str = ""         # what is already recorded about this text
    delivery: str = "copy-for-claude"

    def render(self) -> str:
        """The packet as text, for a human to paste or a worker to read.

        Deterministic: same packet, same bytes. Nothing is interpolated
        that did not come off this dataclass.
        """
        out = [f"# Writing packet — {self.section_title}",
               f"{self.league} · {self.season} · {self.issue_key}"
               + (f" · week {self.week}" if self.week else ""),
               ""]
        if self.format_note:
            out += [f"League format: {self.format_note}", ""]
        out += [f"## What this section is for", self.purpose, ""]
        out += [f"## Who writes it",
                {"commissioner": "The Commissioner writes the prose. Research and "
                                 "suggested angles help; they are not the copy.",
                 "ai": "A drafted section: propose the full prose. The Commissioner "
                       "reviews, edits and accepts it; nothing publishes unread.",
                 "deterministic": "Composed from computed results. Do not rewrite the "
                                  "numbers; only the reading around them is open."
                 }[self.authorship], ""]
        for title, rows in (("Facts on file", self.facts),
                            ("This week's stories", self.stories),
                            ("Continuity", self.continuity),
                            ("What the data cannot support", self.constraints),
                            ("Style", self.style_rules)):
            if rows:
                out += [f"## {title}"] + [f"- {r}" for r in rows] + [""]
        if self.instruction:
            out += ["## What he asked for", self.instruction, ""]
        if self.provenance_note:
            out += ["## Provenance already recorded", self.provenance_note, ""]
        if self.current_prose:
            out += ["## Current text", "", self.current_prose.rstrip(), ""]
        return "\n".join(out).rstrip() + "\n"


def handoff(packet: "WritingPacket", *, paths: dict | None = None) -> str:
    """The packet as the thing a particular assistant is handed.

    One brief, two envelopes. Claude Code runs on this machine, so it is
    given repo-relative paths and reads the evidence itself: that keeps the
    research private by construction, and it can open more than a clipboard
    would carry. ChatGPT is a website, so it gets the packet inline — and
    therefore only what `build()` already guarantees is safe to paste, with
    no path among it.

    Both are told the same purpose, the same authorship rule and the same
    style rules, because those are the parts that decide what comes back.
    """
    if packet.delivery == "copy-for-claude":
        p = paths or {}
        out = [f"Draft the {packet.section_title} section for {packet.league} "
               f"{packet.season} {packet.issue_key}.", ""]
        if p.get("skill"):
            out += [f"1. Read `{p['skill']}` first and follow it. It is the voice",
                    "   authority; nothing else overrides it."]
        if p.get("research"):
            out += [f"2. Read `{p['research']}` for the brief, the evidence and the",
                    "   angles. Every fact in the draft comes from there — find the story,",
                    "   never the numbers."]
        if p.get("proposal"):
            out += [f"3. Write the full section to `{p['proposal']}`,",
                    f"   starting with `<!-- {p.get('marker', 'ROUGH DRAFT')} -->`."]
        if p.get("target"):
            out += [f"4. Do not touch `{p['target']}`. The Commissioner's text is",
                    "   authoritative until he accepts the proposal on the Desk."]
        # Two lines of framing, and no more. The style rules are in the
        # skill this prompt already tells it to read, and the facts are in
        # the brief it already tells it to open; repeating either here is
        # the payload that would put private research in a clipboard.
        out += ["", f"Authorship: {_AUTHORSHIP_LINE[packet.authorship]}."]
        if packet.instruction:
            out += [f"He asked for: {packet.instruction}"]
        out += ["",
                "Line breaks are honored on the published page, so break lines where",
                "you mean to and let paragraphs soft-wrap."]
        return "\n".join(out) + "\n"

    # ChatGPT is a website. Everything it needs travels inline, and nothing
    # that identifies this machine or its private files travels at all.
    body = packet.render()
    return (body + "\n## How to reply\n"
            "Return the section as Markdown and nothing else: no preamble, no "
            "explanation of your choices, no headings that repeat the section "
            "name. The Commissioner pastes your answer into the Desk as a "
            "proposal, reviews it, and decides whether it publishes.\n")


# Short on purpose: this is the one rule that must not depend on which
# button was pressed, and the Claude envelope stays paths, not payload.
_AUTHORSHIP_LINE = {
    "commissioner": "he writes it; research helps and is not the copy",
    "ai": "propose the prose; he reviews and accepts, nothing publishes unread",
    "deterministic": "composed from results; only the reading around them is open",
}


def redact(value: str) -> str:
    """Strip anything path-shaped. One place decides, so one test covers it.

    A packet is written for a private handoff, but it is the kind of thing
    that gets pasted into a chat window, and a filesystem path tells a
    stranger where this machine keeps its editorial state.
    """
    return _PATHISH.sub("[path]", value or "").strip()


def format_note(storage: Storage, league: League) -> str:
    """The league's own shape, read from the synced payload rather than
    hardcoded — the same rule the rest of the app follows."""
    data = storage.get_league(league.league_id) or {}
    positions = [p for p in (data.get("roster_positions") or []) if p != "BN"]
    teams = len(storage.get_rosters(league.league_id) or [])
    rec = (data.get("scoring_settings") or {}).get("rec")
    ppr = ("PPR" if rec == 1 else "half PPR" if rec == 0.5 else
           "standard" if rec in (0, None) else f"{rec} per reception")
    shape = "Superflex" if "SUPER_FLEX" in positions else "1QB"
    return f"{teams}-team {shape}, {ppr}" if teams else f"{shape}, {ppr}"


def purpose_of(section: str, title: str) -> str:
    """One line on what a section is for. Fixed text, like the provenance
    method vocabulary: a free-form purpose is somewhere a private note
    would eventually end up."""
    return {
        "lowdown": "The Commissioner's opening column: what this week was about.",
        "ctp": "The week's matchups, previewed one by one.",
        "power": "Where every team ranks, and why the order changed.",
        "tracks": "Players worth starting, with the reason.",
        "fades": "Players worth benching, with the reason.",
        "hardware": "The week's awards, as decided on the Desk.",
        "blackbox": "What the numbers say that the eye test does not.",
        "forceflow": "The week's roster moves and what they were reading.",
        "false-assumptions": "Claims this league made that the season has tested.",
        "draft-capsules": "Each team's draft, in a paragraph.",
    }.get(section, f"{title}: a section of this week's issue.")


def build(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    section: str,
    *,
    week: int | None = None,
    delivery: str = "copy-for-claude",
    instruction: str = "",
    base_dir=None,
) -> WritingPacket:
    """Assemble one section's packet from what the app already computes.

    Every source here is already used by some existing surface; this puts
    them behind one call so Claude Code and ChatGPT read the same brief.
    Anything unavailable is simply absent — a packet never invents context.
    """
    from leaguepage import ghost_briefs, provenance, section_defaults
    from leaguepage.issue_builder import issue_dir, module_defs_for

    if delivery not in DELIVERY:
        raise ValueError(f"unknown delivery {delivery!r}")
    if week is None and issue_key.startswith("week-"):
        week = int(issue_key.removeprefix("week-"))
    saved = storage.get_issue_modules(league.slug, season, issue_key)
    titles = {k: t for k, t, _kind in module_defs_for(league, issue_key, saved)}
    if section.startswith("matchup:"):
        from leaguepage.issue_builder import matchup_children

        slug = section.split(":", 1)[1]
        kids = (matchup_children(storage, league, season, issue_key, week)
                if week is not None else [])
        title = next((c["title"] for c in kids if c["slug"] == slug), slug)
    else:
        title = titles.get(section, section.replace("-", " ").title())

    prov_row = storage.get_prose_provenance(league.slug, season, issue_key, section)
    origin = provenance.origin_of(prov_row)
    authorship = ("commissioner" if section in COMMISSIONER_AUTHORED
                  or section.startswith("matchup:")
                  else origin if origin in AUTHORSHIP else "ai")

    facts, constraints = [], []
    try:
        brief = ghost_briefs.brief_for_section(storage, league, season, issue_key,
                                               section, week)
    except Exception:
        brief = None
    if brief and brief.get("evidence"):
        facts += [redact(str(e)) for e in brief["evidence"]]
    try:
        for row in section_defaults.evidence_for(storage, league, season, issue_key, section):
            facts.append(redact(f"{row.get('award_name', 'result')}: "
                                f"{row.get('winner') or 'no winner recorded'}"))
    except Exception:
        pass

    stories: list[str] = []
    try:
        from leaguepage.command_brief import brief_data

        for story in (brief_data(storage, league, season, issue_key)["top_stories"] or [])[:5]:
            stories.append(redact(story["headline"]))
    except Exception:
        pass

    continuity: list[str] = []
    try:
        from leaguepage import takes as takes_mod

        names = {rid: v["name"] for rid, v in
                 __import__("leaguepage.team_names", fromlist=["x"])
                 .resolve_public_names(storage, league).items() if v["name"]}
        for r in takes_mod.public_receipts(storage, league.slug, season, names)[:3]:
            continuity.append(redact(f"receipt ({r.get('status')}): {(r.get('quote') or '')[:120]}"))
    except Exception:
        pass

    idir = issue_dir(league, season, issue_key, base_dir)
    path = (idir / "matchups" / section.split(":", 1)[1] / "draft.md"
            if section.startswith("matchup:")
            else idir / "lowdown" / "lowdown.md" if section == "lowdown"
            else idir / "sections" / f"{section}.md")
    current = path.read_text(encoding="utf-8") if path.exists() else ""

    note = ""
    if prov_row:
        st = provenance.classify(prov_row, current)
        note = st["label"] if st else "origin not recorded"

    return WritingPacket(
        league=league.display_name, season=season, issue_key=issue_key,
        section=section, section_title=title, purpose=purpose_of(section, title),
        authorship=authorship, format_note=format_note(storage, league), week=week,
        style_rules=[
            "Follow the Commissioner's voice profile; it is the authority on style.",
            "No em-dashes and no negated parallels.",
            "Every stat carries its source; never invent a number.",
            "Say what the data cannot support rather than working around it.",
        ],
        facts=facts[:40], stories=stories, continuity=continuity,
        constraints=constraints,
        current_prose=current, instruction=redact(instruction),
        provenance_note=note, delivery=delivery)
