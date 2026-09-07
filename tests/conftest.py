from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.storage import Storage

# Every name a developer's private .env might set. Tests must behave the
# same on a machine that has real Supabase credentials configured and on one
# that has none, so the suite is isolated from both .env and the ambient
# environment. Individual tests opt back in with monkeypatch.setenv.
_CONFIG_NAMES = (
    "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY",
    "DATABASE_URL", "LEAGUEPAGE_AUTH_MODE", "LEAGUEPAGE_COMMISSIONER_EMAILS",
    "LEAGUEPAGE_SECRET_KEY", "LEAGUEPAGE_MAIL_PROVIDER",
    "LEAGUEPAGE_MAIL_FROM", "RESEND_API_KEY",
)

# Reaching a live database is opt-in and never implicit. DATABASE_URL is
# stripped like every other credential, so the Postgres contract tests skip
# on an ordinary run even on a machine that has one configured -- which is
# what keeps `pytest tests/` honest. Setting this second name hands them a
# DSN deliberately, and is how the live-validation runs are done:
#
#   LEAGUEPAGE_TEST_DATABASE_URL="$(...)" pytest tests/test_prose_repository.py
#
# Those tests write only to a scratch namespace and delete it either side.
LIVE_DSN_NAME = "LEAGUEPAGE_TEST_DATABASE_URL"


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    from leaguepage import settings

    live = os.environ.get(LIVE_DSN_NAME)
    for name in _CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    if live:
        monkeypatch.setenv("DATABASE_URL", live)
    # point the loader at a path that cannot exist, and reset its cache so
    # the real repo-root .env is never read during tests
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(settings, "_loaded", False)
    yield
    monkeypatch.setattr(settings, "_loaded", False)


@pytest.fixture(autouse=True)
def isolate_editorial_tree(monkeypatch, tmp_path):
    """No test writes into the real `editorial/`.

    One test used to isolate itself by patching `desk.week_dir`, which was
    the module that happened to build the path it wrote through. When prose
    moved behind a repository that resolves the location in one place, that
    patch stopped covering the write and a synthetic matchup draft landed in
    the Commissioner's actual tree. Isolating the root here makes it
    structural: a test that forgets to point somewhere fails its own
    assertions instead of editing real work.

    Tests that want a populated tree still set these themselves; this only
    changes where "unset" points.
    """
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp
    from leaguepage import prose_store

    root = tmp_path / "editorial-default"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", root)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", root)
    prose_store.reset_cache()
    yield
    prose_store.reset_cache()


@pytest.fixture
def storage(tmp_path):
    with Storage(tmp_path / "test.sqlite3") as s:
        yield s
