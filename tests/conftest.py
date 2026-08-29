from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.storage import Storage


@pytest.fixture
def storage(tmp_path):
    with Storage(tmp_path / "test.sqlite3") as s:
        yield s
