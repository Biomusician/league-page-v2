"""Move prose between backends, and prove they agree, without changing either.

This is the tooling a supervised cutover needs and nothing more. It never
switches the authoritative backend: that is `LEAGUEPAGE_PROSE_BACKEND`, and
changing it is a deliberate act with a person watching.

    prose_tool.py inventory              every prose object, key -> path
    prose_tool.py export --to DIR        authoritative store -> Markdown tree
    prose_tool.py import                 filesystem -> postgres (DRY RUN)
    prose_tool.py import --apply         ... for real
    prose_tool.py verify                 compare the two, byte for byte

Three rules hold throughout:

* **Dry run is the default.** `import` reports what it would do and touches
  nothing until `--apply`.
* **Nothing is overwritten silently.** A target that already holds
  different text is reported as a conflict and left alone; `--force` is
  required to replace it, and says so in the output.
* **Prose is never printed.** `verify` reports keys, hashes, versions and
  a status word. The words themselves are the private thing this whole
  system exists to protect, and a comparison tool has no business putting
  them in a terminal or a log.

Research artifacts (briefs, prep, generated packets, caches) are not prose
and are not touched by any subcommand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import prose_store as ps  # noqa: E402
from leaguepage.config import LEAGUES  # noqa: E402


def _issues(base: Path) -> list[tuple[str, str, str]]:
    """Every (league, season, issue) an editorial tree holds."""
    out = []
    if not base.exists():
        return out
    for season_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if not season_dir.name.isdigit():
            continue
        for league in LEAGUES:
            ldir = season_dir / league.slug
            if not ldir.is_dir():
                continue
            for idir in sorted(p for p in ldir.iterdir() if p.is_dir()):
                out.append((league.slug, season_dir.name, idir.name))
    return out


def _all_prose(repo, base: Path) -> dict[str, object]:
    found: dict[str, object] = {}
    for league, season, issue in _issues(base):
        for rec in repo.list_issue(league, season, issue):
            found[str(rec.key)] = rec
    return found


def _base_dir(args) -> Path:
    from leaguepage import issue_builder

    return Path(args.editorial) if args.editorial else Path(issue_builder.EDITORIAL_DIR)


# ------------------------------------------------------------- inventory

def cmd_inventory(args) -> int:
    """Every mutable prose object: its key, and where the filesystem keeps
    it. The round-trip proof that a key and a path name the same thing."""
    base = _base_dir(args)
    repo = ps.FilesystemProseRepository(base_dir=base)
    rows = 0
    for key_text, rec in sorted(_all_prose(repo, base).items()):
        path = ps.path_for(rec.key, base).relative_to(base)
        back = ps.parse_key(key_text)
        assert back == rec.key, f"key does not round-trip: {key_text}"
        assert ps.path_for(back, base) == ps.path_for(rec.key, base)
        print(f"{key_text}\t{path.as_posix()}\t{rec.version}")
        rows += 1
    print(f"# {rows} prose object(s); every key round-trips to one path",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------- export

def cmd_export(args) -> int:
    """The authoritative store, written back out as the repository-shaped
    Markdown tree. Postgres may become the source of truth; a human-readable
    backup in the shape he already knows should not stop existing."""
    out = Path(args.to)
    base = _base_dir(args)
    # The tree being exported FROM is whichever backend is authoritative,
    # but the issue list is always enumerated from the editorial tree,
    # which is the only place that knows what issues exist.
    repo = ps.repository(base_dir=base, backend=args.backend)
    written = 0
    for _key_text, rec in sorted(_all_prose(repo, base).items()):
        target = ps.path_for(rec.key, out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rec.text, encoding="utf-8")
        written += 1
    print(f"exported {written} prose object(s) from the {repo.backend} backend "
          f"to {out}")
    return 0


# ---------------------------------------------------------------- import

def cmd_import(args) -> int:
    """Filesystem tree into Postgres. Dry run unless --apply."""
    base = _base_dir(args)
    source = ps.FilesystemProseRepository(base_dir=base)
    target = ps.repository(backend=ps.POSTGRES)
    create = update = same = conflict = 0
    for _key_text, rec in sorted(_all_prose(source, base).items()):
        existing = target.get(rec.key)
        if not existing.exists:
            create += 1
            if args.apply:
                target.put(rec.key, rec.text, expected_version=None,
                           source="import", keep_history=False)
        elif existing.content_hash == rec.content_hash:
            same += 1
        elif args.force:
            update += 1
            if args.apply:
                target.put(rec.key, rec.text,
                           expected_version=existing.version,
                           source="import-force", keep_history=True)
        else:
            # Something is already there and it is not what we are carrying.
            # Overwriting it would destroy an edit made in the target, which
            # is exactly what a cutover must never do by accident.
            conflict += 1
            print(f"CONFLICT {rec.key}: target holds different text "
                  f"(use --force to replace)")
    verb = "applied" if args.apply else "dry run"
    print(f"{verb}: {create} to create, {update} to replace, {same} already "
          f"identical, {conflict} conflict(s)")
    return 1 if conflict and not args.force else 0


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    """Compare the two backends without changing either.

    Exit code 1 on any mismatch, so it can gate a cutover.
    """
    base = _base_dir(args)
    fs = ps.FilesystemProseRepository(base_dir=base)
    pg = ps.repository(backend=ps.POSTGRES)
    left = _all_prose(fs, base)
    right = {}
    for league, season, issue in _issues(base):
        for rec in pg.list_issue(league, season, issue):
            right[str(rec.key)] = rec
    statuses = {"same": 0, "filesystem-only": 0, "postgres-only": 0,
                "content-differs": 0}
    for key_text in sorted(set(left) | set(right)):
        a, b = left.get(key_text), right.get(key_text)
        if a and not b:
            status = "filesystem-only"
        elif b and not a:
            status = "postgres-only"
        elif a.content_hash == b.content_hash:
            status = "same"
        else:
            status = "content-differs"
        statuses[status] += 1
        if status != "same" or args.verbose:
            # Hashes and versions only. The prose itself never appears.
            print(f"{status:16s} {key_text}\t"
                  f"fs={(a.content_hash[:12] if a else '-')}\t"
                  f"pg={(b.content_hash[:12] if b else '-')}\t"
                  f"fsver={(a.version if a else '-')}\t"
                  f"pgver={(b.version if b else '-')}")
    print("  ".join(f"{k}={v}" for k, v in statuses.items()))
    bad = sum(v for k, v in statuses.items() if k != "same")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--editorial", help="editorial tree (default: configured)")
    subs = ap.add_subparsers(dest="cmd", required=True)

    p = subs.add_parser("inventory", help="every prose key and its path")
    p.set_defaults(fn=cmd_inventory)

    p = subs.add_parser("export", help="authoritative store -> Markdown tree")
    p.add_argument("--to", required=True)
    p.add_argument("--backend", choices=[ps.FILESYSTEM, ps.POSTGRES])
    p.set_defaults(fn=cmd_export)

    p = subs.add_parser("import", help="filesystem -> postgres")
    p.add_argument("--apply", action="store_true", help="write; default is a dry run")
    p.add_argument("--force", action="store_true",
                   help="replace target text that differs (destructive)")
    p.set_defaults(fn=cmd_import)

    p = subs.add_parser("verify", help="compare backends; nonzero on mismatch")
    p.add_argument("--verbose", action="store_true", help="list matches too")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except ps.ProseError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
