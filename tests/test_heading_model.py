"""One page, one H1, and it names the page.

The masthead carries the league's name on every built page. As an `<h1>`
that made every page claim the league as its title and left the page's
actual subject at `<h2>`, so a heading list read "DISCO CHAT / Standings"
on the standings page and "DISCO CHAT / Week 01" on both the front page and
the issue page -- three different pages announcing the same title.

The model now:

* the persistent masthead is branding, not a heading;
* every substantive page owns exactly one `<h1>`, and it is the page's own
  subject;
* levels descend one at a time from there.

Checked against the BUILT SITE rather than the templates, because the
failure it guards against is compositional: a template that is correct on
its own can still emit a skipped level once it is inside `base.html`. Two
of the four pages that failed this when it was written were exactly that --
`matchups` and `transactions` had been skipping a level since before the
masthead was ever the question.
"""
from __future__ import annotations

import re

from test_site_build import _build, site_env  # noqa: F401  (fixture import)

H_RE = re.compile(r"<h([1-6])[ >]")
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)


def _levels(html: str) -> list[int]:
    return [int(n) for n in H_RE.findall(html)]


def _h1(html: str) -> str:
    m = H1_RE.search(html)
    return re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""


def _built(site_env):
    db, tmp = site_env
    _build(db, tmp)
    dist = tmp / "dist"
    return dist, sorted(dist.rglob("*.html"))


def test_every_page_has_exactly_one_h1(site_env):
    dist, pages = _built(site_env)
    bad = [(str(p.relative_to(dist)), n) for p in pages
           if (n := len(H1_RE.findall(p.read_text(encoding="utf-8")))) != 1]
    assert not bad, bad[:8]


def test_no_page_skips_a_heading_level(site_env):
    dist, pages = _built(site_env)
    bad = []
    for p in pages:
        lv = _levels(p.read_text(encoding="utf-8"))
        skips = [f"h{a}->h{b}" for a, b in zip(lv, lv[1:]) if b - a > 1]
        if skips:
            bad.append((str(p.relative_to(dist)), skips[:2]))
    assert not bad, bad[:8]


def test_the_first_heading_on_a_page_is_its_h1(site_env):
    dist, pages = _built(site_env)
    bad = [(str(p.relative_to(dist)), _levels(p.read_text(encoding="utf-8"))[:3])
           for p in pages
           if _levels(p.read_text(encoding="utf-8"))[:1] not in ([], [1])]
    assert not bad, bad[:8]


def test_the_masthead_is_branding_and_not_a_heading(site_env):
    """It is byte-identical on every page of a league, so it cannot be the
    title of any of them."""
    dist, pages = _built(site_env)
    seen = 0
    for p in pages:
        html = p.read_text(encoding="utf-8")
        if 'class="masthead"' not in html:
            continue
        seen += 1
        head = html[html.index('class="masthead"'):html.index("</header>")]
        assert "<h1" not in head, str(p.relative_to(dist))
        assert 'class="brand"' in head, str(p.relative_to(dist))
    assert seen > 10, f"only {seen} pages carried a masthead"


def test_the_h1_names_the_page_rather_than_the_league(site_env):
    """"Exactly one h1" is satisfiable by putting the league's name in it,
    so spot-check that the h1 is the page's own subject."""
    dist, _pages = _built(site_env)
    league = next(d.name for d in dist.iterdir()
                  if d.is_dir() and (d / "standings" / "index.html").exists())
    for rel, want in {"standings/index.html": "Standings",
                      "teams/index.html": "Teams",
                      "black-box/index.html": "Black Box",
                      "transactions/index.html": "Force Flow",
                      "archive/index.html": "Archive"}.items():
        page = dist / league / rel
        assert page.exists(), rel
        assert _h1(page.read_text(encoding="utf-8")) == want, rel
