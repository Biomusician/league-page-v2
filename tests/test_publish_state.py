"""What the publish page says about production.

Production changes only through a deploy, so "when did a reader last see
something new" is the latest deploy record that reached production, and
"is the latest revision live" is a revision number on that record.
"""
from __future__ import annotations

import datetime as dt
import json

from leaguepage import publish_jobs as pj
from leaguepage.storage import Storage


def test_ago_reads_like_a_person():
    now = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)

    def at(**kw):
        return (now - dt.timedelta(**kw)).isoformat()

    assert pj.ago(at(seconds=20), now=now) == "just now"
    assert pj.ago(at(minutes=1), now=now) == "1 minute ago"
    assert pj.ago(at(minutes=10), now=now) == "10 minutes ago"
    assert pj.ago(at(hours=3), now=now) == "3 hours ago"
    assert pj.ago(at(days=5), now=now) == "5 days ago"
    assert pj.ago("garbage", now=now) == "at an unknown time"


def test_last_public_change_is_the_latest_deploy_that_reached_production(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        s.set_meta("deploy_state:disco:2026:week-01", json.dumps(
            {"state": "deployed", "at": "2026-09-01T10:00:00+00:00", "revision": 1}))
        s.set_meta("deploy_state:disco:2026:week-02", json.dumps(
            {"state": "deployed-unverified", "at": "2026-09-08T10:00:00+00:00", "revision": 1}))
        s.set_meta("deploy_state:disco:2026:week-03", json.dumps(
            {"state": "never-deployed", "at": None,
             "last_attempt": {"at": "2026-09-15T10:00:00+00:00", "failed_stage": "build"}}))
        s.set_meta("deploy_state:surfeit:2026:week-01", json.dumps(
            {"state": "deployed", "at": "2026-09-20T10:00:00+00:00", "revision": 1}))
        now = dt.datetime(2026, 9, 13, 10, 0, tzinfo=dt.timezone.utc)
        best = pj.last_public_change(s, "disco", now=now)
        assert best["issue_key"] == "week-02" and best["ago"] == "5 days ago"
        assert best["revision"] == 1
        assert pj.last_public_change(s, "nobody", now=now) is None


def test_revision_number_reads_the_family_file_names(tmp_path):
    assert pj.revision_number(tmp_path / "week-01.json") == 1
    assert pj.revision_number(tmp_path / "week-01.r3.json") == 3
