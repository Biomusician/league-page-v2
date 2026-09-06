"""Commissioner-editable prose, addressed logically rather than by path.

(`prose.py` next door renders prose to HTML and is a different concern;
this module decides where the words live and who is allowed to change
them.)

The Desk has always known that the Lowdown is a file at
`editorial/2026/disco/week-02/lowdown/lowdown.md`. Six different places in
the code built that path, two of them disagreed about where a matchup
draft lives, and only one of them checked that the result stayed inside
the editorial tree. That is workable while the only machine editing prose
is this one, and it is the last thing standing between here and a Desk the
Commissioner can open from a phone.

So prose gets an identity that is not a path. A `ProseKey` names one
editable object: which league, which season, which issue, what kind of
thing, and which one. `ProseRepository` is the only way to read or write
it. Where the bytes actually live is the backend's business.

Four kinds, and only four. Everything the Commissioner writes or publishes:

    section    the Lowdown and every module's prose
    matchup    a week's matchup previews, which he writes by product rule
    proposal   what Claude Code or ChatGPT hands back, awaiting his verdict

Research is deliberately not here. Briefs, prep, command briefs, generated
packets and caches are evidence, not publication state, and putting them
behind this interface would turn "stop writing prose to disk" into "put
every generated file in a database".

**Versions and hashes are different facts**, and the split is load-bearing:

    version        a storage token. It changes whenever the stored bytes
                   change, and it is the thing an edit is allowed to be
                   based on. Callers never interpret it; they read one and
                   hand it back.
    content_hash   editorial identity, normalised the way provenance has
                   always normalised it: comments stripped, line endings
                   unified, outer whitespace dropped. Approval, provenance
                   and "changed since published" ask this, because removing
                   an HTML comment is not an edit.

A save carries the version it started from. If the stored version has moved,
the write is refused with `ProseConflict` rather than merged, because the
alternative is a phone silently overwriting a laptop.

The filesystem backend derives its version from the content itself rather
than counting mutations. That is deliberate: `editorial/` is a git working
tree that Jonathan edits in a text editor and Claude Code writes proposals
into, so a counter kept beside the file cannot notice an edit that did not
come through the Desk, and a stale save would quietly clobber it. A token
derived from the bytes notices every change whoever made it. Postgres has
no such back door, so it counts, which is cheaper and is what its `sections`
table already carries.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from leaguepage import provenance

# ------------------------------------------------------------------- kinds

SECTION, MATCHUP, PROPOSAL = "section", "matchup", "proposal"
KINDS = (SECTION, MATCHUP, PROPOSAL)

# The vocabulary the editorial metadata tables have always used for a
# section, kept exactly: prose_revisions, section_prose_state,
# prose_provenance and issue_modules are all keyed by these strings, and
# this tranche is not a metadata migration.
_NAME_RE = re.compile(r"^[a-z0-9-]+$")
_MATCHUP_SECTION_RE = re.compile(r"^matchup:([a-z0-9-]+)$")

# Backends
FILESYSTEM, POSTGRES = "filesystem", "postgres"
BACKEND_SETTING = "LEAGUEPAGE_PROSE_BACKEND"


class ProseError(Exception):
    """Something about the request was wrong, before any storage was asked."""


class ProseConflict(Exception):
    """The stored prose moved after the caller read it.

    Carries both sides, because a conflict the Commissioner cannot see is
    a conflict he will resolve by guessing. Nothing is merged and nothing
    is discarded here; the decision is his.
    """

    def __init__(self, key: "ProseKey", expected: str | None,
                 actual: str | None, current_text: str = ""):
        super().__init__(f"{key} changed: expected {expected!r}, stored {actual!r}")
        self.key = key
        self.expected = expected
        self.actual = actual
        self.current_text = current_text


# -------------------------------------------------------------------- key

@dataclass(frozen=True)
class ProseKey:
    """One editable object, named without reference to any filesystem.

    Stable across a public title being renamed, a change of backend, a
    change of operating system, and an export back to Markdown files.
    """
    league: str
    season: str
    issue: str
    kind: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ProseError(f"unknown prose kind: {self.kind!r}")
        if not (self.league and self.season and self.issue):
            raise ProseError("a prose key needs a league, a season and an issue")
        if self.kind == PROPOSAL:
            if not _is_section_id(self.name):
                raise ProseError(f"not a section a proposal can target: {self.name!r}")
        elif not _NAME_RE.match(self.name):
            raise ProseError(f"not a usable prose name: {self.name!r}")

    # -- constructors, so callers never assemble one by hand ------------

    @classmethod
    def section(cls, league: str, season: str, issue: str, name: str) -> "ProseKey":
        return cls(league, season, issue, SECTION, name)

    @classmethod
    def matchup(cls, league: str, season: str, issue: str, slug: str) -> "ProseKey":
        return cls(league, season, issue, MATCHUP, slug)

    @classmethod
    def proposal(cls, league: str, season: str, issue: str,
                 section_id: str) -> "ProseKey":
        return cls(league, season, issue, PROPOSAL, section_id)

    @classmethod
    def for_section(cls, league: str, season: str, issue: str,
                    section_id: str) -> "ProseKey":
        """From the legacy section string the Desk passes around.

        `fades` and `lowdown` are sections; `matchup:<slug>` is a matchup.
        This is the bridge that let every existing call site keep its
        signature while the storage underneath it changed.
        """
        m = _MATCHUP_SECTION_RE.match(section_id)
        if m:
            return cls.matchup(league, season, issue, m.group(1))
        return cls.section(league, season, issue, section_id)

    # -- identity ------------------------------------------------------

    @property
    def section_id(self) -> str:
        """The string `prose_revisions`, `section_prose_state`,
        `prose_provenance` and `issue_modules` are keyed by. A proposal
        answers with the section it is a proposal *for*."""
        if self.kind == MATCHUP:
            return f"matchup:{self.name}"
        return self.name

    @property
    def target(self) -> "ProseKey":
        """What a proposal would be accepted into. Itself, for anything else."""
        if self.kind != PROPOSAL:
            return self
        return ProseKey.for_section(self.league, self.season, self.issue, self.name)

    def __str__(self) -> str:
        return f"{self.league}/{self.season}/{self.issue}/{self.kind}/{self.name}"


def _is_section_id(value: str) -> bool:
    return bool(_NAME_RE.match(value) or _MATCHUP_SECTION_RE.match(value))


def parse_key(text: str) -> ProseKey:
    """The inverse of `str(key)`, for tools that carry keys as text."""
    parts = text.split("/")
    if len(parts) != 5:
        raise ProseError(f"not a prose key: {text!r}")
    league, season, issue, kind, name = parts
    return ProseKey(league, season, issue, kind, name)


# ----------------------------------------------------------------- record

@dataclass(frozen=True)
class Prose:
    key: ProseKey
    text: str
    version: str | None          # None means: no such object
    updated_at: str | None = None

    @property
    def exists(self) -> bool:
        return self.version is not None

    @property
    def content_hash(self) -> str:
        """Editorial identity, computed when something asks.

        It was stored on the record until measurement showed every read
        paying for a normalising pass and a second hash it usually never
        used: assembling a full preview reads seventeen sections and asks
        for the hash of none of them. It is a pure function of the text,
        so deriving it costs nothing to correctness.
        """
        return content_hash(self.text)


def content_hash(text: str) -> str:
    """Editorial identity. The same normalisation provenance has always
    used, so approval and 'changed since published' keep their meaning."""
    return provenance.text_sha(text)


# ------------------------------------------------------------- contract

class ProseRepository(Protocol):
    """Every way the application touches Commissioner-editable prose.

    No path, connection, cursor or file object crosses this line, so a
    caller cannot tell which backend it holds. Conflicts raise; absence
    does not (`get` on a missing object returns a record whose `exists` is
    False, because "not written yet" is an ordinary state here).
    """

    backend: str

    def get(self, key: ProseKey) -> Prose: ...

    def exists(self, key: ProseKey) -> bool: ...

    def put(self, key: ProseKey, text: str, *, expected_version: str | None,
            source: str = "commissioner-save", keep_history: bool = True) -> Prose:
        """Store new text for `key`, refusing if it moved since
        `expected_version`. Pass None to mean "this must not exist yet".

        Preserving the prior text as a revision is part of this operation
        and not a separate call, because an edit that promises history must
        not be able to half-happen.
        """

    def delete(self, key: ProseKey, *, expected_version: str | None = None) -> bool:
        """Remove an object entirely. Distinct from storing empty text:
        a discarded proposal stops existing, a cleared section does not."""

    def list_issue(self, league: str, season: str, issue: str, *,
                   kinds: tuple[str, ...] = KINDS) -> list[Prose]:
        """Everything stored for one issue, in one pass."""

    def history(self, key: ProseKey, limit: int = 10) -> list[dict]: ...

    def revision(self, revision_id: int) -> dict | None: ...

    def revision_counts(self, league: str, season: str,
                        issue: str) -> dict[str, int]:
        """{section_id: how many undo steps} for one issue, in one pass.

        Part of the contract rather than a caller's loop: the editor page
        needs a count on every card, and a per-section call is a query per
        card against whichever store is live.
        """

    def health(self) -> dict:
        """Safe status facts for diagnostics: reachable, schema current.
        Never a path, a host, a DSN or a credential."""


# ------------------------------------------------------ filesystem backend

def _editorial_root(base_dir: Path | None = None) -> Path:
    # Read at call time, not captured at construction: the tests point
    # EDITORIAL_DIR at a temporary tree, and a repository built before that
    # happened must still land in the right place.
    from leaguepage import issue_builder

    return Path(base_dir) if base_dir else Path(issue_builder.EDITORIAL_DIR)


def path_for(key: ProseKey, base_dir: Path | None = None) -> Path:
    """The one place a prose path is built.

    Mirrors what the Desk has always done, including refusing a matchup on
    an issue that has no week, and including the containment check that
    only one of the six previous builders performed.
    """
    root = _editorial_root(base_dir)
    idir = root / key.season / key.league / key.issue
    if key.kind == MATCHUP:
        if not re.match(r"^week-\d+$", key.issue):
            raise ProseError(f"matchups belong to a week, not {key.issue!r}")
        path = idir / "matchups" / key.name / "draft.md"
    elif key.kind == PROPOSAL:
        # ':' is illegal in a Windows filename; matchup proposals have
        # always spelled it '--'.
        path = idir / "proposals" / f"{key.name.replace(':', '--')}.md"
    elif key.name == "lowdown":
        path = idir / "lowdown" / "lowdown.md"
    else:
        path = idir / "sections" / f"{key.name}.md"
    if not _within(path, root):
        raise ProseError(f"{key} would escape the editorial tree")
    return path


def _within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def version_of(text: str) -> str:
    """The filesystem's storage token: the exact bytes, hashed.

    Exact rather than normalised, because concurrency is about whether the
    stored bytes moved, not about whether the writing changed. A trailing
    newline is a new version and the same content.
    """
    return "fs1:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class FilesystemProseRepository:
    """Markdown files under `editorial/`, which is what runs today.

    Its optimistic concurrency is real rather than decorative: the version
    is derived from the bytes on disk, so an edit made outside the Desk is
    seen as a conflict rather than silently lost. Atomicity is scoped to
    this process, which is exactly its deployment model. The hosted future
    does not use this backend.
    """

    backend = FILESYSTEM

    def __init__(self, db_path=None, *, base_dir: Path | None = None):
        self.db_path = db_path
        self.base_dir = base_dir
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- reads ---------------------------------------------------------

    def get(self, key: ProseKey) -> Prose:
        path = path_for(key, self.base_dir)
        # One stat answers both "is it there" and "when did it change";
        # asking separately cost a syscall per section, and assembling a
        # preview reads seventeen of them.
        try:
            stat = path.stat()
        except OSError:
            return Prose(key=key, text="", version=None)
        # read_text decodes universal newlines, so a file written CRLF and
        # a file written LF read identically and do not fake a conflict.
        text = path.read_text(encoding="utf-8")
        return Prose(key=key, text=text, version=version_of(text),
                     updated_at=_stamp(stat.st_mtime))

    def exists(self, key: ProseKey) -> bool:
        return path_for(key, self.base_dir).exists()

    def list_issue(self, league: str, season: str, issue: str, *,
                   kinds: tuple[str, ...] = KINDS) -> list[Prose]:
        root = _editorial_root(self.base_dir)
        idir = root / season / league / issue
        out: list[Prose] = []
        if not idir.exists():
            return out
        if SECTION in kinds:
            if (idir / "lowdown" / "lowdown.md").exists():
                out.append(self.get(ProseKey.section(league, season, issue, "lowdown")))
            for p in sorted((idir / "sections").glob("*.md")):
                if _NAME_RE.match(p.stem):
                    out.append(self.get(ProseKey.section(league, season, issue, p.stem)))
        if MATCHUP in kinds and re.match(r"^week-\d+$", issue):
            for d in sorted((idir / "matchups").glob("*")):
                if d.is_dir() and (d / "draft.md").exists() and _NAME_RE.match(d.name):
                    out.append(self.get(ProseKey.matchup(league, season, issue, d.name)))
        if PROPOSAL in kinds:
            for p in sorted((idir / "proposals").glob("*.md")):
                section_id = p.stem.replace("--", ":", 1) if p.stem.startswith("matchup--") \
                    else p.stem
                if _is_section_id(section_id):
                    out.append(self.get(
                        ProseKey.proposal(league, season, issue, section_id)))
        return out

    # -- writes --------------------------------------------------------

    def put(self, key: ProseKey, text: str, *, expected_version: str | None,
            source: str = "commissioner-save", keep_history: bool = True) -> Prose:
        path = path_for(key, self.base_dir)
        with self._lock(key):
            current = self.get(key)
            if current.version != expected_version:
                raise ProseConflict(key, expected_version, current.version,
                                    current.text)
            if current.exists and text == current.text:
                # Saving the same bytes twice is not an edit: no revision,
                # no mutation, no invalidated approval.
                return current
            if keep_history and current.text:
                # Before the write, not after. If the write then fails, an
                # extra revision is harmless; the reverse loses history.
                self._add_revision(key, current.text, source)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return Prose(key=key, text=text, version=version_of(text),
                     updated_at=_stamp(path.stat().st_mtime))

    def delete(self, key: ProseKey, *, expected_version: str | None = None) -> bool:
        path = path_for(key, self.base_dir)
        with self._lock(key):
            current = self.get(key)
            if expected_version is not None and current.version != expected_version:
                raise ProseConflict(key, expected_version, current.version,
                                    current.text)
            if not current.exists:
                return False
            path.unlink()
        return True

    # -- history -------------------------------------------------------

    def health(self) -> dict:
        """Safe facts only. Not the editorial root: a filesystem path is
        exactly the kind of private detail diagnostics leak."""
        root = _editorial_root(self.base_dir)
        return {"reachable": root.exists(), "schema_current": True}

    def history(self, key: ProseKey, limit: int = 10) -> list[dict]:
        with self._storage() as s:
            return s.get_prose_revisions(key.league, key.season, key.issue,
                                         key.section_id, limit)

    def revision(self, revision_id: int) -> dict | None:
        with self._storage() as s:
            return s.get_prose_revision(revision_id)

    def revision_counts(self, league: str, season: str,
                        issue: str) -> dict[str, int]:
        with self._storage() as s:
            return s.prose_revision_counts(league, season, issue)

    # -- internals -----------------------------------------------------

    def _add_revision(self, key: ProseKey, prior: str, source: str) -> None:
        if key.kind == PROPOSAL:
            # A proposal is a draft awaiting a verdict, not a published
            # object with an undo history. Accepting one writes a revision
            # of the section it lands in, which is where history belongs.
            return
        with self._storage() as s:
            s.add_prose_revision(key.league, key.season, key.issue,
                                 key.section_id, prior, source)

    def _storage(self):
        from leaguepage.config import DB_PATH
        from leaguepage.storage import Storage

        return Storage(self.db_path or DB_PATH)

    def _lock(self, key: ProseKey) -> threading.Lock:
        # Two request threads editing the same section would otherwise be
        # able to interleave read-check-write. Process-scoped, which is
        # what a filesystem backend can honestly promise.
        with self._locks_guard:
            return self._locks.setdefault(str(key), threading.Lock())


def _stamp(mtime: float) -> str | None:
    import datetime as dt

    try:
        return dt.datetime.fromtimestamp(
            mtime, dt.timezone.utc).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


# ------------------------------------------------------------ selection

_CACHE: dict[tuple, ProseRepository] = {}


def backend_name() -> str:
    """Which store is authoritative. Filesystem unless deliberately told
    otherwise: pulling main must never move the source of truth."""
    from leaguepage import settings

    return (settings.get(BACKEND_SETTING) or FILESYSTEM).strip().lower()


def repository(db_path=None, *, base_dir: Path | None = None,
               backend: str | None = None) -> ProseRepository:
    """The one authoritative repository for this run.

    Exactly one backend is live. There is no dual-write and no fallback:
    if Postgres is selected and cannot be reached, this raises rather than
    quietly writing to the filesystem, because a silent fallback is how
    two half-populated sources of truth are created.
    """
    name = (backend or backend_name())
    if name == FILESYSTEM:
        cached = _CACHE.get((FILESYSTEM, str(db_path), str(base_dir)))
        if cached is None:
            cached = FilesystemProseRepository(db_path, base_dir=base_dir)
            _CACHE[(FILESYSTEM, str(db_path), str(base_dir))] = cached
        return cached
    if name == POSTGRES:
        from leaguepage.prose_postgres import PostgresProseRepository

        return PostgresProseRepository(db_path)
    raise ProseError(
        f"unknown prose backend {name!r}: set {BACKEND_SETTING} to "
        f"{FILESYSTEM!r} or {POSTGRES!r}")


def reset_cache() -> None:
    """Tests point EDITORIAL_DIR somewhere new between cases."""
    _CACHE.clear()


def iter_issue_keys(league: str, season: str, issue: str,
                    base_dir: Path | None = None) -> Iterator[ProseKey]:
    """Every prose key an issue directory holds, for tools that need to
    enumerate without reading content."""
    repo = FilesystemProseRepository(base_dir=base_dir)
    for prose in repo.list_issue(league, season, issue):
        yield prose.key
