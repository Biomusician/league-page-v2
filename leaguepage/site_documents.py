"""Site-wide authored copy: one document, addressed by a slug.

There is exactly one today -- `about`, the public methodology and AI
disclosure page -- and this module exists because that one document was the
last authoring surface writing authoritative state to a filesystem. On a
hosted Desk that file does not survive a restart, so the page describing how
the paper is made is the one thing Hosted Beta would have discarded.

WHAT THIS IS NOT

Not a CMS. No revisions, no drafts, no publish workflow, no ordering, no
per-league copy, no titles, no routing. A second document is a row. A second
FEATURE is a decision, and it should not be reachable by accident because
this module was built wide enough to allow it.

WHERE IT LIVES

  filesystem  editorial/site/<slug>.md, git-tracked and in the backup
              bundle, exactly as before.
  postgres    site_documents, one row per slug (migration 0007).

Reads go through `read()`, which answers with the shipped default when
nothing has been written -- the same contract the filesystem version always
had. Writes go through `EditorialState.set_site_document` inside a store
action, so the About save is one transaction owned by the store like every
other authoring route, and the Postgres path asserts the signed-in
Commissioner before it writes.
"""
from __future__ import annotations

from pathlib import Path

from leaguepage import prose_store

# The only slug the product authors. Named rather than spelled out at each
# call site so a typo is an ImportError instead of a silently empty page.
ABOUT = "about"


def path_for(slug: str = ABOUT, base_dir: Path | None = None) -> Path:
    """Where the filesystem backend keeps this document.

    `issue_builder.EDITORIAL_DIR` is read as an attribute at call time, the
    same seam the research artifacts use, so a test that redirects the
    editorial tree redirects this too.
    """
    if not slug.replace("-", "").isalnum():
        raise ValueError(f"not a site document slug: {slug!r}")
    from leaguepage import issue_builder

    return (base_dir or issue_builder.EDITORIAL_DIR) / "site" / f"{slug}.md"


# The shipped default, not the Commissioner's copy: he can replace all of
# it from the Desk at Site -> About, and then this is never read again.
#
# Written as the site's own methodology note rather than in his voice,
# because it is a disclosure about how the paper is made and not a piece of
# editorial writing. It says only what is already visible on the site --
# the six provenance labels the issue pages carry, where the numbers come
# from, and that published issues are immutable. Nothing here describes how
# any of it is built.
#
# One paragraph per list entry, unwrapped: `prose.render` honours a line
# break the way it does inside an issue, so wrapping this to 72 columns
# would publish the wraps as <br>.
_ABOUT_BLOCKS = [
    "# About League Page",

    "League Page is a weekly newspaper for the fantasy football leagues it"
    " covers. Each issue is written, edited and published by the league's"
    " Commissioner.",

    "## Who writes it",

    "The Commissioner has final say over everything published here. No"
    " section is published automatically, and nothing reaches a published"
    " issue without the Commissioner's approval.",

    "Some of the work behind an issue is assisted by AI: gathering"
    " research, summarising what has changed in a league, and preparing"
    " drafts to be rewritten, cut or thrown away. Other sections are"
    " assembled from the league's own data with no writing involved at all."
    " And some are written from scratch.",

    "Those are different things, so each section says which it was.",

    "## The line under each heading",

    "Every section in an issue carries a short line naming where it came"
    " from and whether the Commissioner edited it. There are six, and no"
    " others:",

    "\n".join([
        "- **Commish-written** — the Commissioner's words.",
        "- **Commish-written · AI-assisted** — the Commissioner's"
        " words, with AI help somewhere behind them.",
        "- **AI-generated** — drafted by AI and published without edits.",
        "- **AI-generated · Commish edited** — drafted by AI, then"
        " edited.",
        "- **Automatically generated** — assembled from league data, not"
        " written.",
        "- **Automatically generated · Commish edited** — assembled"
        " from data, then edited.",
    ]),

    "The line describes the section it sits under, not the issue as a whole."
    " One issue routinely carries several different ones.",

    "## Where the numbers come from",

    "Rosters, lineups, scores, standings, transactions and draft results"
    " come from Sleeper, where the leagues are played. Anything derived from"
    " them — power rankings, positional strength, draft value, records"
    " — is computed from that data, and the page it appears on says what"
    " it was measured against.",

    "Rankings and awards are editorial judgments rather than measurements."
    " Where a page shows a computed ordering beside the Commissioner's, it"
    " shows both and names the disagreement rather than settling it quietly.",

    "## The archive",

    "Published issues are permanent. An issue is frozen when it is published"
    " and is not rewritten afterwards. A correction is published as a new"
    " revision beside the original, and the issue says that it was updated"
    " and why. Older issues, including ones written before this site"
    " existed, are kept in the archive as they were written.",

    "## Your team",

    "Choosing your team stores that one choice in your own browser. There is"
    " no account and no sign-in, nothing is sent anywhere, and clearing it"
    " removes it.",
]

DEFAULT_ABOUT = "\n\n".join(_ABOUT_BLOCKS) + "\n"


def read(slug: str = ABOUT, *, base_dir: Path | None = None,
         backend: str | None = None, dsn: str | None = None) -> str:
    """The authoritative body, or the shipped default if nobody has written.

    No actor, because this is also how the public build reads the page --
    the same shape as an unbound prose read, and for the same reason.
    """
    name = (backend or prose_store.backend_name()).strip().lower()
    if name == prose_store.FILESYSTEM:
        p = path_for(slug, base_dir)
        return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_ABOUT
    if name == prose_store.POSTGRES:
        body = _read_postgres(slug, dsn)
        return DEFAULT_ABOUT if body is None else body
    raise prose_store.ProseError(
        f"unknown backend for site documents: {name!r}")


def _read_postgres(slug: str, dsn: str | None) -> str | None:
    import psycopg

    from leaguepage import settings

    value = dsn or settings.get(settings.DATABASE_URL)
    if not value:
        raise prose_store.ProseError(
            "site documents are set to postgres but DATABASE_URL is not "
            "configured. Refusing to fall back to the filesystem: two "
            "half-populated sources of truth is worse than not starting.")
    with psycopg.connect(value, connect_timeout=20) as conn:
        row = conn.execute("select body from site_documents where slug = %s",
                           (slug,)).fetchone()
    return row[0] if row else None
