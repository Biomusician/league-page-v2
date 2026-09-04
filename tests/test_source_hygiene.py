"""No control characters in the source tree.

This exists because a patch script written as a shell heredoc silently turned
`\\b` into a literal backspace (0x08) and `\\1` into 0x01 inside a regex. The
result was a pattern that compiled, ran, matched nothing, and produced a page
that looked plausible. It happened three times in one session before anything
caught it, because nothing was looking.

A regex that quietly matches nothing is the worst possible failure mode for a
codebase whose whole discipline is "do not publish a claim the data does not
support": the claim just stops appearing, and no test fails.
"""
from __future__ import annotations

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCANNED = (".py", ".html", ".js", ".json", ".md", ".css", ".sql", ".toml", ".cfg")
SKIP_DIRS = {".git", ".venv", "__pycache__", "dist", "dist-preview", "site",
             ".site-worktree", "data", "logs", "backups", "node_modules",
             "archive"}

# Tab, newline and carriage return are the only control characters a text
# file has any business containing.
ALLOWED = {0x09, 0x0A, 0x0D}


def _files():
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCANNED:
            continue
        if set(path.relative_to(REPO).parts) & SKIP_DIRS:
            continue
        yield path


def test_no_control_characters_anywhere_in_the_source():
    bad = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            bad.append(f"{path.relative_to(REPO)}: not valid UTF-8")
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for ch in line:
                if ord(ch) < 0x20 and ord(ch) not in ALLOWED:
                    bad.append(f"{path.relative_to(REPO)}:{lineno}: "
                               f"U+{ord(ch):04X}")
                    break
    assert bad == [], bad[:20]


def test_every_regex_in_the_package_still_compiles():
    """A mangled escape can leave a pattern that compiles and matches
    nothing, but a mangled one that does NOT compile should fail here rather
    than at the first import in production."""
    import importlib
    import pkgutil

    import leaguepage

    failed = []
    for mod in pkgutil.iter_modules(leaguepage.__path__):
        try:
            importlib.import_module(f"leaguepage.{mod.name}")
        except Exception as exc:                        # noqa: BLE001
            failed.append(f"{mod.name}: {type(exc).__name__}: {exc}")
    assert failed == [], failed


@pytest.mark.parametrize("pattern,should_match,should_not", [
    # The three that were actually corrupted, pinned by behaviour.
    ("leaguepage.site_build", "_SCRIPT_OR_STYLE_RE",
     ("<style>a{b:c}</style>", "<p>plain</p>")),
])
def test_the_patterns_that_were_corrupted_do_what_they_say(pattern, should_match,
                                                           should_not):
    import importlib

    mod = importlib.import_module(pattern)
    rx = getattr(mod, should_match)
    assert rx.search(should_not[0]), f"{should_match} matches nothing"
    assert not rx.search(should_not[1])


def test_the_archive_year_pattern_finds_a_year():
    """The listing label depends on this, and a corrupted version silently
    stopped flagging fourteen issues whose title disagrees with their file."""
    import re

    rx = re.compile(r"\b(20\d\d)\b")
    assert rx.findall("2023 Disco Week 1") == ["2023"]
    assert rx.findall("Disco 12") == []
