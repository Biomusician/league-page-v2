"""The league selector.

This page asks one question: which league do you want to enter. Two doors
and a quiet way to the About page is the whole of it.

It used to answer that question badly. The standfirst paragraph was a flex
sibling of the card grid on a centred row, so it sat out in the left margin
as a floating column of text nobody had placed there on purpose; and the
grid had `gap:0`, which made the two leagues one rectangle with a seam down
it rather than two destinations.

The page styles itself and shares no rules with either league site, which
is what keeps a change here off a masthead or a standings table there.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.site_build import build_site
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("landing")
    db = tmp / "t.sqlite3"
    with Storage(db) as s:
        for slug in ("disco", "surfeit"):
            lg = get_league(slug)
            populate_league(s, lg, teams=10, rounds=3, picks="complete", season=SEASON)
            populate_matchups(s, lg, week=1, teams=10,
                              scores={rid: 90.0 + rid for rid in range(1, 11)})
        build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                   editorial_dir=tmp / "editorial")
    return tmp / "dist"


@pytest.fixture(scope="module")
def root(built):
    return (built / "index.html").read_text(encoding="utf-8")


# ------------------------------------------------------------ what is gone

def test_the_floating_standfirst_is_gone(root):
    """It was the visible symptom: a paragraph of copy stranded in the left
    margin because it was a flex sibling of the cards, not part of them."""
    body = root[root.index("<body>"):]      # the comment above explains it
    assert "standfirst" not in body
    assert "A weekly paper for two fantasy football leagues" not in body
    assert "the argument about all three" not in body


def test_no_marketing_copy_was_invented_to_replace_it(root):
    """The league choices carry the page. Nothing else is needed above the
    fold and nothing decorative was added to fill the space."""
    for banned in ("Same game", "different universes", "lives here",
                   "Rank / Debate", "Higher standards", "One obsession"):
        assert banned.lower() not in root.lower(), banned


def test_the_page_grew_no_navigation(root):
    """This is a selector, not a site with a nav bar."""
    assert "<nav" not in root
    hrefs = re.findall(r'<a[^>]+href="([^"]+)"', root)
    assert sorted(hrefs) == ["#leagues", "about/index.html",
                             "disco/index.html", "surfeit/index.html"]


# ------------------------------------------------------------ two doors

def test_each_league_is_its_own_card(root):
    assert root.count('class="card disco"') == 1
    assert root.count('class="card surfeit"') == 1
    assert "Disco Chat" in root and "The Surfeit" in root


def test_the_cards_do_not_touch(root):
    """A single rectangle split down the middle is what this replaced, so
    the grid has to carry a real gap."""
    css = root[root.index("<style>"):root.index("</style>")]
    gap = re.search(r"\.leagues\s*\{[^}]*gap:([^;]+);", css)
    assert gap, "the league grid declares no gap"
    assert "0" != gap.group(1).strip()
    assert "clamp(" in gap.group(1)


def test_each_card_states_its_league_and_format(root):
    for name, fmt in (("Disco Chat", "12 Teams &middot; Superflex"),
                      ("The Surfeit", "10 Teams &middot; Half PPR")):
        assert name in root and fmt in root


def test_each_card_carries_an_obvious_action(root):
    assert root.count("Enter League") == 2


def test_both_cards_enter_the_right_league(root):
    assert 'href="disco/index.html"' in root
    assert 'href="surfeit/index.html"' in root
    disco = root[root.index('class="card disco"'):root.index('class="card surfeit"')]
    assert "disco/index.html" in disco and "surfeit/index.html" not in disco


def test_the_logos_share_one_media_frame(root):
    """A wide banner and a round badge. Fixing the frame and containing the
    image inside it is what keeps the two cards the same height."""
    css = root[root.index("<style>"):root.index("</style>")]
    assert re.search(r"\.media\s*\{[^}]*height:", css)
    assert "object-fit:contain" in css.replace(" ", "")
    assert root.count('class="media"') == 2


def test_the_action_sits_at_the_bottom_of_both_cards(root):
    """`margin-top:auto` is what lines the two buttons up whatever happens
    above them."""
    css = root[root.index("<style>"):root.index("</style>")]
    assert re.search(r"\.enter\s*\{[^}]*margin-top:auto", css)


def test_each_league_keeps_its_own_colours(root):
    css = root[root.index("<style>"):root.index("</style>")]
    assert "#15142c" in css and "#f0d848" in css      # disco field and gold
    assert "#0a0d14" in css and "#f0c419" in css      # surfeit field and gold
    assert "#4da3e8" in css                            # surfeit roundel blue


# ------------------------------------------------------------ behaviour

def test_the_whole_card_is_one_link_with_nothing_interactive_inside(root):
    """A card that is one link cannot trap a keyboard or split its own
    accessible name, and there is no nested control to go wrong."""
    for cls in ("card disco", "card surfeit"):
        start = root.index(f'class="{cls}"')
        card = root[start:root.index("</a>", start)]
        assert "<a " not in card and "<button" not in card


def test_the_logos_are_decorative_because_the_name_is_next_to_them(root):
    assert root.count('alt=""') == 2


def test_focus_is_indicated_and_not_only_on_hover(root):
    css = root[root.index("<style>"):root.index("</style>")]
    assert "a.card:focus" in css
    assert re.search(r"a\.card:focus\s*\{[^}]*outline:", css)


def test_motion_is_restrained_and_can_be_turned_off(root):
    css = root[root.index("<style>"):root.index("</style>")]
    assert "prefers-reduced-motion" in css
    reduced = css[css.index("prefers-reduced-motion"):]
    assert "transform:none" in reduced.replace(" ", "")
    # nothing that bounces, scales up or glows
    for banned in ("@keyframes", "scale(", "animation:"):
        assert banned not in css, banned


def test_it_stacks_before_the_cards_run_out_of_room(root):
    css = root[root.index("<style>"):root.index("</style>")]
    stack = re.search(r"@media \(max-width: *([\d.]+)rem\)", css)
    assert stack, "no stacking breakpoint"
    assert float(stack.group(1)) >= 30, "stacks too late to help a tablet"
    assert "grid-template-columns:1fr" in css.replace(" ", "")


# ------------------------------------------------------------ about

def test_about_is_built_and_reachable_from_the_footer(built, root):
    assert 'href="about/index.html"' in root
    assert (built / "about" / "index.html").exists()


def test_the_footer_offers_about_and_nothing_else(root):
    footer = root[root.index("<footer>"):root.index("</footer>")]
    assert ">About<" in footer
    assert "Donate" not in footer and "Support" not in footer


def test_about_is_a_restrained_placeholder(built):
    page = (built / "about" / "index.html").read_text(encoding="utf-8")
    assert "About League Page" in page
    assert "Information about the project will be added here." in page
    assert "Back to league select" in page


def test_no_donation_destination_is_invented(built):
    """A support section renders only once a real URL is configured, so
    nothing here can ship as a dead or made-up donation button."""
    page = (built / "about" / "index.html").read_text(encoding="utf-8")
    for host in ("paypal", "venmo", "ko-fi", "kofi", "buymeacoffee",
                 "patreon", "cash.app", "gofundme", "stripe"):
        assert host not in page.lower(), host
    assert cfg.SUPPORT_URL == "", "a real URL belongs to the Commissioner, not to a default"
    assert "Support the Project" not in page


def test_the_support_section_appears_once_a_url_is_configured(tmp_path, monkeypatch):
    """The extension point works; it is simply not switched on."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(cfg.TEMPLATES_DIR)), autoescape=True)
    page = env.get_template("public/about.html").render(
        site_url="https://example.invalid", support_url="https://example.invalid/give",
        support_label="Support the project")
    assert "Support the Project" in page
    assert 'href="https://example.invalid/give"' in page


# ------------------------------------------------------------ no spillover

def test_the_selector_shares_no_stylesheet_with_the_leagues(built, root):
    """Requirement: a change here must not reach a league page. It cannot,
    because this page links no stylesheet and defines its own."""
    assert "<link rel=stylesheet" not in root and 'rel="stylesheet"' not in root
    for slug in ("disco", "surfeit"):
        home = (built / slug / "index.html").read_text(encoding="utf-8")
        for cls in ("class=\"card disco\"", "class=\"leagues\"", "h1.mark"):
            assert cls not in home, f"{slug} home picked up a selector class"


def test_the_league_pages_still_link_their_own_theme(built):
    for slug in ("disco", "surfeit"):
        home = (built / slug / "index.html").read_text(encoding="utf-8")
        assert f"{slug}.css" in home
