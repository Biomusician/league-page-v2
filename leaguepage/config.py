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


LEAGUES: list[League] = [
    League(
        slug="disco",
        display_name="DISCO CHAT",
        league_id="1355356729629495296",
        theme="disco",
        subtitle="Operational / CRC",
        adp_source="fantasypros_ecr_redraft_superflex",
    ),
    League(
        slug="surfeit",
        display_name="THE SURFEIT",
        league_id="1367544788303253504",
        theme="surfeit",
        subtitle="Force Design 2035",
        adp_source="fantasypros_ecr_redraft_half_ppr",
    ),
]

LEAGUES_BY_SLUG = {l.slug: l for l in LEAGUES}
LEAGUES_BY_ID = {l.league_id: l for l in LEAGUES}


def get_league(slug: str) -> League:
    try:
        return LEAGUES_BY_SLUG[slug]
    except KeyError as exc:
        raise KeyError(f"Unknown league slug {slug!r}; add it to leaguepage/config.py") from exc
