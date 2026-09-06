"""Prose has an identity, a version, and exactly one home.

The behavioural contract is written once and run against every backend the
machine can reach, because a caller that can tell which store it is talking
to is a caller that will eventually depend on the answer. Postgres is
skipped rather than faked when DATABASE_URL is unset: a green test against
a store that was never contacted is worse than an honest gap.
"""
from __future__ import annotations

import subprocess
import sys
import threading

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import prose_store as ps
from leaguepage import settings
from leaguepage.config import REPO_ROOT

# ------------------------------------------------------------------ keys


def test_a_key_is_not_a_path_and_survives_a_rename():
    """A public title can change on any Tuesday. The key cannot."""
    k = ps.ProseKey.section("disco", "2026", "week-02", "fades")
    assert str(k) == "disco/2026/week-02/section/fades"
    assert ps.parse_key(str(k)) == k
    assert k.section_id == "fades"           # what the metadata tables use
    assert "\\" not in str(k) and ":" not in str(k)


def test_the_four_shapes_map_to_the_paths_the_product_already_uses(tmp_path):
    base = tmp_path / "editorial"

    def rel(key):
        return ps.path_for(key, base).relative_to(base).as_posix()

    assert rel(ps.ProseKey.section("disco", "2026", "week-02", "lowdown")) == \
        "2026/disco/week-02/lowdown/lowdown.md"
    assert rel(ps.ProseKey.section("disco", "2026", "week-02", "fades")) == \
        "2026/disco/week-02/sections/fades.md"
    assert rel(ps.ProseKey.matchup("disco", "2026", "week-02", "a-vs-b")) == \
        "2026/disco/week-02/matchups/a-vs-b/draft.md"
    assert rel(ps.ProseKey.proposal("disco", "2026", "week-02", "fades")) == \
        "2026/disco/week-02/proposals/fades.md"
    # ':' is illegal in a Windows filename and always has been spelled '--'
    assert rel(ps.ProseKey.proposal("disco", "2026", "week-02", "matchup:a-vs-b")) == \
        "2026/disco/week-02/proposals/matchup--a-vs-b.md"


def test_a_matchup_section_string_round_trips_through_the_key():
    k = ps.ProseKey.for_section("disco", "2026", "week-02", "matchup:a-vs-b")
    assert k.kind == ps.MATCHUP and k.name == "a-vs-b"
    assert k.section_id == "matchup:a-vs-b"
    assert ps.ProseKey.proposal("disco", "2026", "week-02", "matchup:a-vs-b").target == k


def test_a_key_cannot_escape_the_editorial_tree():
    for bad in ("../../../etc/passwd", "..", "a/b", "Fades", "with space", ""):
        with pytest.raises(ps.ProseError):
            ps.ProseKey.section("disco", "2026", "week-02", bad)


def test_matchups_belong_to_a_week(tmp_path):
    key = ps.ProseKey.matchup("disco", "2026", "draft", "a-vs-b")
    with pytest.raises(ps.ProseError):
        ps.path_for(key, tmp_path)


# -------------------------------------------------- the backend contract

@pytest.fixture(params=[ps.FILESYSTEM, ps.POSTGRES])
def repo(request, tmp_path, monkeypatch):
    """One contract, every reachable backend."""
    if request.param == ps.POSTGRES:
        if not settings.get(settings.DATABASE_URL):
            pytest.skip("DATABASE_URL is not configured; postgres not reachable")
        from leaguepage.prose_postgres import PostgresProseRepository

        return PostgresProseRepository()
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    ps.reset_cache()
    return ps.FilesystemProseRepository(db_path=tmp_path / "t.sqlite3")


def _k(name="fades", kind=ps.SECTION):
    return ps.ProseKey("disco", "2026", "week-02", kind, name)


def test_absent_is_not_empty(repo):
    """Two different states, and the difference decides whether a save is
    a creation or an overwrite."""
    rec = repo.get(_k())
    assert not rec.exists and rec.text == "" and rec.version is None
    repo.put(_k(), "", expected_version=None)
    rec = repo.get(_k())
    assert rec.exists and rec.text == "" and rec.version is not None


def test_a_write_must_say_which_version_it_is_based_on(repo):
    first = repo.put(_k(), "One.\n", expected_version=None)
    with pytest.raises(ps.ProseConflict) as exc:
        repo.put(_k(), "Two.\n", expected_version=None)
    assert exc.value.actual == first.version
    assert exc.value.current_text == "One.\n"
    second = repo.put(_k(), "Two.\n", expected_version=first.version)
    assert second.version != first.version
    assert repo.get(_k()).text == "Two.\n"


def test_the_stale_writer_loses_and_nothing_is_merged(repo):
    """The whole point. A phone and a laptop holding version 1: one wins,
    the other is told, and neither text is silently combined."""
    v1 = repo.put(_k(), "Original.\n", expected_version=None)
    laptop = phone = v1.version
    repo.put(_k(), "From the phone.\n", expected_version=phone)
    with pytest.raises(ps.ProseConflict):
        repo.put(_k(), "From the laptop.\n", expected_version=laptop)
    assert repo.get(_k()).text == "From the phone.\n"


def test_only_one_of_many_racing_writers_wins(repo):
    base = repo.put(_k(), "Start.\n", expected_version=None)
    winners, lock = [], threading.Lock()
    gate = threading.Barrier(6)

    def write(n):
        gate.wait(5)
        try:
            repo.put(_k(), f"Version from writer {n}.\n",
                     expected_version=base.version)
            with lock:
                winners.append(n)
        except ps.ProseConflict:
            pass

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(winners) == 1
    assert repo.get(_k()).text == f"Version from writer {winners[0]}.\n"


def test_saving_the_same_text_twice_is_not_an_edit(repo):
    v1 = repo.put(_k(), "Same.\n", expected_version=None)
    v2 = repo.put(_k(), "Same.\n", expected_version=v1.version)
    assert v2.version == v1.version
    assert len(repo.history(_k())) == 0          # nothing to undo


def test_a_whitespace_only_change_is_a_new_version_and_the_same_content(repo):
    """Version and content hash answer different questions, and this is
    the case that proves they must."""
    v1 = repo.put(_k(), "Words.\n", expected_version=None)
    v2 = repo.put(_k(), "Words.\n\n", expected_version=v1.version)
    assert v2.version != v1.version              # the store moved
    assert v2.content_hash == v1.content_hash    # the writing did not


def test_history_is_kept_by_the_write_that_replaced_it(repo):
    v1 = repo.put(_k(), "First.\n", expected_version=None)
    repo.put(_k(), "Second.\n", expected_version=v1.version)
    hist = repo.history(_k())
    assert [h["prior_text"] for h in hist] == ["First.\n"]
    assert hist[0]["source"] == "commissioner-save"


def test_a_refused_write_keeps_no_history(repo):
    """A conflict is not an edit, so it must not leave one behind."""
    v1 = repo.put(_k(), "First.\n", expected_version=None)
    repo.put(_k(), "Second.\n", expected_version=v1.version)
    with pytest.raises(ps.ProseConflict):
        repo.put(_k(), "Third.\n", expected_version=v1.version)
    assert len(repo.history(_k())) == 1
    assert repo.get(_k()).text == "Second.\n"


def test_clearing_is_not_deleting(repo):
    v1 = repo.put(_k(), "Words.\n", expected_version=None)
    cleared = repo.put(_k(), "", expected_version=v1.version)
    assert repo.exists(_k()) and cleared.text == ""
    assert repo.delete(_k()) is True
    assert not repo.exists(_k())
    assert repo.delete(_k()) is False


def test_every_kind_is_storable_and_they_do_not_collide(repo):
    section = _k("fades")
    proposal = _k("fades", ps.PROPOSAL)
    matchup = _k("a-vs-b", ps.MATCHUP)
    repo.put(section, "His words.\n", expected_version=None)
    repo.put(proposal, "A draft for the same section.\n", expected_version=None)
    repo.put(matchup, "The preview.\n", expected_version=None)
    assert repo.get(section).text == "His words.\n"
    assert repo.get(proposal).text == "A draft for the same section.\n"
    assert repo.get(matchup).text == "The preview.\n"
    listed = {str(r.key) for r in repo.list_issue("disco", "2026", "week-02")}
    assert listed == {str(section), str(proposal), str(matchup)}


def test_a_proposal_keeps_no_undo_history_of_its_own(repo):
    """A proposal is a draft awaiting a verdict. The history that matters
    is the section's, written when one is accepted into it."""
    p = _k("fades", ps.PROPOSAL)
    v1 = repo.put(p, "First attempt.\n", expected_version=None)
    repo.put(p, "Second attempt.\n", expected_version=v1.version)
    assert repo.history(p) == []


def test_state_survives_a_new_repository_instance(repo, tmp_path):
    """Nothing is cached in the process. A restart sees the same version."""
    v1 = repo.put(_k(), "Durable.\n", expected_version=None)
    if repo.backend == ps.FILESYSTEM:
        fresh = ps.FilesystemProseRepository(db_path=tmp_path / "t.sqlite3")
    else:
        from leaguepage.prose_postgres import PostgresProseRepository

        fresh = PostgresProseRepository()
    assert fresh.get(_k()).version == v1.version
    with pytest.raises(ps.ProseConflict):
        fresh.put(_k(), "x", expected_version=None)


def test_health_reports_status_and_never_a_path_or_a_secret(repo):
    h = repo.health()
    assert set(h) >= {"reachable", "schema_current"}
    blob = repr(h)
    # `://` rather than each scheme spelled out: stricter (a health payload
    # should carry no URL at all) and it keeps a DSN-shaped literal out of a
    # tracked file, which is what scripts/audit_repo_privacy.py scans for.
    for leak in ("C:\\", "/home", "://", "password"):
        assert leak not in blob


# ------------------------------------------------- filesystem specifics

def test_line_endings_do_not_fake_a_conflict(tmp_path, monkeypatch):
    """A file written CRLF and one written LF read the same, so the version
    is the same and an edit based on either is accepted."""
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    ps.reset_cache()
    repo = ps.FilesystemProseRepository(db_path=tmp_path / "t.sqlite3")
    key = _k()
    path = ps.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"One line.\r\nAnother.\r\n")
    crlf = repo.get(key)
    path.write_bytes(b"One line.\nAnother.\n")
    lf = repo.get(key)
    assert crlf.text == lf.text and crlf.version == lf.version


def test_an_edit_made_outside_the_desk_is_seen(tmp_path, monkeypatch):
    """`editorial/` is a working tree he edits by hand and Claude Code
    writes into. A version the store cannot notice would let a stale save
    silently destroy one of those edits."""
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    ps.reset_cache()
    repo = ps.FilesystemProseRepository(db_path=tmp_path / "t.sqlite3")
    key = _k()
    v1 = repo.put(key, "Through the Desk.\n", expected_version=None)
    ps.path_for(key).write_text("Edited in a text editor.\n", encoding="utf-8")
    with pytest.raises(ps.ProseConflict):
        repo.put(key, "Stale save from a tab.\n", expected_version=v1.version)


# ------------------------------------------------------------ selection

def test_the_default_backend_is_the_filesystem(monkeypatch):
    """Pulling main must never move the source of truth."""
    monkeypatch.delenv(ps.BACKEND_SETTING, raising=False)
    monkeypatch.setattr(settings, "get", lambda name, default=None: None)
    assert ps.backend_name() == ps.FILESYSTEM


def test_an_unknown_backend_is_refused_rather_than_guessed(monkeypatch):
    with pytest.raises(ps.ProseError):
        ps.repository(backend="mongo")


def test_postgres_without_a_dsn_fails_closed(monkeypatch):
    """Never a silent fallback to the filesystem: that is how two
    half-populated sources of truth are created."""
    from leaguepage.prose_postgres import PostgresProseRepository

    monkeypatch.setattr(settings, "get", lambda name, default=None: None)
    with pytest.raises(ps.ProseError) as exc:
        PostgresProseRepository().get(_k())
    assert "DATABASE_URL" in str(exc.value)
    assert "filesystem" in str(exc.value)


def test_a_postgres_token_is_not_a_filesystem_token():
    from leaguepage import prose_postgres as pg

    assert pg.parse_token("pg1:7") == 7
    with pytest.raises(ps.ProseError):
        pg.parse_token("fs1:deadbeef")


def test_the_postgres_write_is_one_guarded_statement():
    """Read-then-update on a later line is the bug two hosted instances
    would find first, so the shape is pinned even where the database
    cannot be reached to run it."""
    src = (REPO_ROOT / "leaguepage" / "prose_postgres.py").read_text(encoding="utf-8")
    assert "version=version+1" in src
    assert "and section=%s and version=%s" in src
    assert "on conflict (league_slug, season, issue_key, kind, section)" in src


# -------------------------------------------------------- tools on disk

def _tool(*args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "prose_tool.py"), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT))


def test_the_inventory_round_trips_every_key_to_one_path(tmp_path):
    base = tmp_path / "editorial"
    (base / "2026" / "disco" / "week-02" / "sections").mkdir(parents=True)
    (base / "2026" / "disco" / "week-02" / "sections" / "fades.md").write_text(
        "Words.\n", encoding="utf-8")
    out = _tool("--editorial", str(base), "inventory")
    assert out.returncode == 0, out.stderr
    assert "disco/2026/week-02/section/fades" in out.stdout
    assert "sections/fades.md" in out.stdout


def test_export_writes_a_repository_shaped_tree(tmp_path):
    base = tmp_path / "editorial"
    (base / "2026" / "disco" / "week-02" / "lowdown").mkdir(parents=True)
    (base / "2026" / "disco" / "week-02" / "lowdown" / "lowdown.md").write_text(
        "The Lowdown.\n", encoding="utf-8")
    out_dir = tmp_path / "backup"
    out = _tool("--editorial", str(base), "export", "--to", str(out_dir))
    assert out.returncode == 0, out.stderr
    copied = out_dir / "2026" / "disco" / "week-02" / "lowdown" / "lowdown.md"
    assert copied.read_text(encoding="utf-8") == "The Lowdown.\n"


def test_import_is_a_dry_run_until_told_otherwise(tmp_path):
    """It must be impossible to move the source of truth by running a tool
    without reading its flags."""
    base = tmp_path / "editorial"
    (base / "2026" / "disco" / "week-02" / "sections").mkdir(parents=True)
    (base / "2026" / "disco" / "week-02" / "sections" / "fades.md").write_text(
        "Words.\n", encoding="utf-8")
    out = _tool("--editorial", str(base), "import")
    # With no DATABASE_URL it refuses rather than inventing a target; with
    # one it reports counts. Either way it does not write on this path.
    assert out.returncode in (0, 2)
    assert "--apply" not in out.stdout or "dry run" in out.stdout
    if out.returncode == 2:
        assert "DATABASE_URL" in out.stderr


def test_the_verifier_never_prints_prose(tmp_path):
    """It compares private writing. Keys, hashes and versions are enough
    to find a mismatch, and are all it is allowed to say."""
    src = (REPO_ROOT / "scripts" / "prose_tool.py").read_text(encoding="utf-8")
    body = src.split("def cmd_verify", 1)[1].split("def main", 1)[0]
    assert ".text" not in body
    assert "content_hash[:12]" in body


def test_take_candidates_still_come_from_the_rough_drafts(tmp_path, monkeypatch):
    """The review packet's own heading says "flagged in the rough drafts".

    Routing prose through the repository nearly emptied that section: the
    rough draft is research and stays on disk, while the prose moved. Both
    sources are read, and this is the test that noticed.
    """
    import leaguepage.issue_builder as ib_mod
    from leaguepage.review_packet import _take_candidates

    base = tmp_path / "editorial"
    idir = base / "2026" / "disco" / "week-02"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "sections").mkdir()
    monkeypatch.setattr(ib_mod, "EDITORIAL_DIR", base)
    ps.reset_cache()
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        "Draft.\n\nTAKE CANDIDATE: the rough draft still counts.\n",
        encoding="utf-8")
    (idir / "lowdown" / "PREP.md").write_text("no candidates here\n",
                                              encoding="utf-8")
    (idir / "lowdown" / "lowdown.md").write_text(
        "TAKE CANDIDATE: so does the finished Lowdown.\n", encoding="utf-8")
    (idir / "sections" / "fades.md").write_text(
        "TAKE CANDIDATE: and a section.\n", encoding="utf-8")

    found = _take_candidates(idir)
    assert len(found) == 3, found
    assert any("rough draft still counts" in f and "rough-lowdown.md" in f
               for f in found)
    assert any("finished Lowdown" in f and "lowdown.md" in f for f in found)
    assert any("and a section" in f and "fades.md" in f for f in found)


# ------------------------------------------- history lives with the prose

def test_history_and_restore_read_the_store_that_wrote_them(repo):
    """Undo is part of the prose, not a separate database.

    Both backends write a revision inside the write that replaced the
    text, so both must answer for it too. This ran green while the Desk
    was still asking SQLite directly, which is precisely why it is here:
    the contract was right and two callers went around it.
    """
    key = _k("fades")
    first = repo.put(key, "One.\n", expected_version=None)
    repo.put(key, "Two.\n", expected_version=first.version)

    rows = repo.history(key)
    assert [r["prior_text"] for r in rows] == ["One.\n"]
    assert rows[0]["source"] == "commissioner-save"

    one = repo.revision(rows[0]["id"])
    assert one is not None
    assert one["prior_text"] == "One.\n"
    assert (one["league_slug"], one["season"], one["issue_key"], one["section"]) \
        == (key.league, key.season, key.issue, key.section_id)


def test_revision_counts_answer_for_a_whole_issue_in_one_call(repo):
    """The editor page needs a count on every card. Asking per card was a
    query -- and on the filesystem backend a connection -- per card."""
    a, b = _k("fades"), _k("tracks")
    first = repo.put(a, "One.\n", expected_version=None)
    repo.put(a, "Two.\n", expected_version=first.version)
    repo.put(b, "Only once.\n", expected_version=None)

    counts = repo.revision_counts(a.league, a.season, a.issue)
    assert counts.get(a.section_id) == 1
    assert b.section_id not in counts, "a first write replaces nothing"


# ------------------------------------------------- research is not prose

def test_research_files_are_not_prose_and_the_repository_never_holds_them(
        tmp_path, monkeypatch):
    """The lowdown directory holds one piece of prose and four pieces of
    research, and the repository must be able to tell them apart.

    `themes.md`, `outline.md`, `rough-lowdown.md` and `PREP.md` are
    evidence a Claude Code session left on this machine. They are not
    publication state, they are not versioned, and a section named
    `themes` is a section file -- it can never resolve onto the research
    one.
    """
    base = tmp_path / "editorial"
    idir = base / "2026" / "disco" / "week-02"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "sections").mkdir()
    (idir / "matchups" / "a-vs-b" / "generated").mkdir(parents=True)
    monkeypatch.setattr(ib, "EDITORIAL_DIR", base)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", base)
    ps.reset_cache()

    for name in ("PREP.md", "AUTHORING.md", "themes.md", "outline.md",
                 "rough-lowdown.md"):
        (idir / "lowdown" / name).write_text(f"research: {name}\n",
                                             encoding="utf-8")
    (idir / "lowdown" / "lowdown.md").write_text("The prose.\n", encoding="utf-8")
    (idir / "matchups" / "a-vs-b" / "commissioner_notes.md").write_text(
        "His own notes.\n", encoding="utf-8")
    (idir / "matchups" / "a-vs-b" / "draft.md").write_text("The preview.\n",
                                                           encoding="utf-8")
    (idir / "matchups" / "a-vs-b" / "generated" / "data.json").write_text(
        "{}", encoding="utf-8")

    repo = ps.repository(base_dir=base)
    found = {str(r.key) for r in repo.list_issue("disco", "2026", "week-02")}
    assert found == {
        str(ps.ProseKey.section("disco", "2026", "week-02", "lowdown")),
        str(ps.ProseKey.matchup("disco", "2026", "week-02", "a-vs-b")),
    }

    # A section called "themes" is a section file. It cannot reach the
    # research file that happens to share its name.
    themes = ps.ProseKey.section("disco", "2026", "week-02", "themes")
    assert ps.path_for(themes, base) == idir / "sections" / "themes.md"
    assert not repo.get(themes).exists


def test_nothing_in_the_application_switches_its_own_prose_backend():
    """A cutover is a deployment decision, never a code path.

    The setting is read in exactly one place and written nowhere. If a
    module ever sets it, one run could be reading a different store than
    the next and the split brain would be invisible.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    assign = re.compile(
        r"(environ\s*\[\s*['\"]LEAGUEPAGE_PROSE_BACKEND|"
        r"setenv\s*\(\s*['\"]LEAGUEPAGE_PROSE_BACKEND|"
        r"BACKEND_SETTING\s*\]\s*=|setenv\s*\(\s*\w*BACKEND_SETTING)")
    offenders = []
    for folder in ("leaguepage", "scripts"):
        for path in sorted((root / folder).rglob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if assign.search(line):
                    offenders.append(f"{path.relative_to(root)}:{n}")
    assert offenders == [], offenders
