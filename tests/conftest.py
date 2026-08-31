from __future__ import annotations

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


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    from leaguepage import settings

    for name in _CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    # point the loader at a path that cannot exist, and reset its cache so
    # the real repo-root .env is never read during tests
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(settings, "_loaded", False)
    yield
    monkeypatch.setattr(settings, "_loaded", False)


@pytest.fixture
def storage(tmp_path):
    with Storage(tmp_path / "test.sqlite3") as s:
        yield s
