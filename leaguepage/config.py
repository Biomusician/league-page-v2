"""League registry and paths for the League Page.

Two leagues, one engine. Anything the Sleeper API can report at runtime
(scoring, roster slots, playoff format, superflex) is NOT recorded here —
it is read from the synced /league payload. This file holds only identity,
routing, and theme facts that Sleeper cannot know.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "league.sqlite3"
ARCHIVE_DIR = REPO_ROOT / "archive"
EDITORIAL_DIR = REPO_ROOT / "editorial"
SITE_DIR = REPO_ROOT / "site"
PUBLISHED_DIR = REPO_ROOT / "published"  # frozen snapshots of published issues
DIST_DIR = REPO_ROOT / "dist"            # deployable public build output
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"

SEASON = "2026"


@dataclass(frozen=True)
class League:
    slug: str            # URL segment and internal key: "disco" / "surfeit"
    display_name: str    # masthead name
    league_id: str       # current-season Sleeper league id
    theme: str           # theme pack key, matches static/themes/<theme>.css later
    subtitle: str        # standing masthead subtitle
    adp_source: str = ""  # refdata/adp/<key>.json reference-rank snapshot
    # The author's own team. Editorial rule: the person writing the
    # newsletter does not headline it — his matchup is barred from FEATURE
    # unless it carries real playoff consequences (see matchup_interest.
    # author_matchup_stakes). This is a publication fact, not a league
    # setting: Sleeper's is_owner flag marks every commissioner, including
    # co-commissioners who are not the author. Roster IDs are public.
    author_roster_id: int | None = None


# Public comments (giscus / GitHub Discussions) on published issue pages.
# All values are PUBLIC by design (giscus config is client-side); no secrets.
# To activate: create a public comments-only GitHub repo (NEVER the private
# League-Page source), enable Discussions, install the giscus app on it,
# then paste the four values from https://giscus.app here. Empty repo =
# comments disabled everywhere; imported historical archive pages never get
# comments; list "league:season:issue" in disabled_issues to opt one out.
COMMENTS = {
    "repo": "",            # e.g. "biomusician/league-page-comments"
    "repo_id": "",
    "category": "",        # e.g. "Announcements" or a dedicated "Issues" category
    "category_id": "",
    "disabled_issues": [],
}

LEAGUES: list[League] = [
    League(
        slug="disco",
        display_name="DISCO CHAT",
        league_id="1355356729629495296",
        theme="disco",
        subtitle="Operational / League Control and Reporting Center",
        adp_source="fantasypros_ecr_redraft_superflex",
        author_roster_id=1,
    ),
    League(
        slug="surfeit",
        display_name="THE SURFEIT",
        league_id="1367544788303253504",
        theme="surfeit",
        subtitle="Future Fantasy Force Design",
        adp_source="fantasypros_ecr_redraft_half_ppr",
        author_roster_id=1,
    ),
]

LEAGUES_BY_SLUG = {l.slug: l for l in LEAGUES}
LEAGUES_BY_ID = {l.league_id: l for l in LEAGUES}


def get_league(slug: str) -> League:
    try:
        return LEAGUES_BY_SLUG[slug]
    except KeyError as exc:
        raise KeyError(f"Unknown league slug {slug!r}; add it to leaguepage/config.py") from exc
