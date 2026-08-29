from __future__ import annotations

import pytest

import leaguepage.publish as publish
from leaguepage.publish import PublishError, ROUGH_DRAFT_MARKER, approve, mark_edited, publish_issue

from fixtures import TEST_LEAGUE


@pytest.fixture
def editorial_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "EDITORIAL_DIR", tmp_path / "editorial")
    return tmp_path


def _write_issue(tmp_path, text: str):
    src = tmp_path / "editorial" / "2026" / "testleague" / "draft" / "issue.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    return src


def test_marker_blocks_edited_status(storage, editorial_tmp):
    _write_issue(editorial_tmp, f"<!-- {ROUGH_DRAFT_MARKER} -->\n# Issue\n")
    with pytest.raises(PublishError, match="ROUGH DRAFT"):
        mark_edited(storage, TEST_LEAGUE, "2026", "draft")


def test_publish_requires_approval(storage, editorial_tmp):
    _write_issue(editorial_tmp, "# The Draft Issue\n\nClean copy.\n")
    mark_edited(storage, TEST_LEAGUE, "2026", "draft")
    with pytest.raises(PublishError, match="approve"):
        publish_issue(storage, TEST_LEAGUE, "2026", "draft", site_dir=editorial_tmp / "site")


def test_full_lifecycle_renders_html(storage, editorial_tmp):
    _write_issue(editorial_tmp, "# The Draft Issue\n\n**Common Tactical Picture** is live.\n")
    mark_edited(storage, TEST_LEAGUE, "2026", "draft")
    approve(storage, TEST_LEAGUE, "2026", "draft")
    out = publish_issue(storage, TEST_LEAGUE, "2026", "draft", site_dir=editorial_tmp / "site")
    html = out.read_text(encoding="utf-8")
    assert "<h1>The Draft Issue</h1>" in html
    assert "TEST LEAGUE" in html
    issue = storage.get_issue("testleague", "2026", "draft")
    assert issue["status"] == "published"
    assert issue["published_path"].endswith("draft.html")


def test_marker_reintroduced_blocks_publish(storage, editorial_tmp):
    src = _write_issue(editorial_tmp, "# Clean\n")
    mark_edited(storage, TEST_LEAGUE, "2026", "draft")
    approve(storage, TEST_LEAGUE, "2026", "draft")
    # someone regenerates over the approved file — publish must refuse
    src.write_text(f"<!-- {ROUGH_DRAFT_MARKER} -->\n# Regenerated\n", encoding="utf-8")
    with pytest.raises(PublishError, match="ROUGH DRAFT"):
        publish_issue(storage, TEST_LEAGUE, "2026", "draft", site_dir=editorial_tmp / "site")


def test_missing_source_is_clear_error(storage, editorial_tmp):
    with pytest.raises(PublishError, match="issue.md"):
        mark_edited(storage, TEST_LEAGUE, "2026", "draft")
