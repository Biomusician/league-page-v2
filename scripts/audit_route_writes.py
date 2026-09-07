"""Static call-graph pass: which stores can each Desk route write?

The wide net. Approximate by construction -- Python is dynamic -- so the
authority on what a route ACTUALLY does is
`tests/test_hosted_mutation_audit.py`, which drives each route and watches
every write. The two are run together because they fail differently: this
pass reaches branches a test may not, and the test catches dispatch this
pass cannot see.

    .venv/Scripts/python.exe scripts/audit_route_writes.py [out.json]

Sinks:
  sqlite      a Storage method whose body contains INSERT/UPDATE/DELETE
  filesystem  write_text / write_bytes / unlink / mkdir / rmtree / rename
  prose       ProseRepository.put / .delete

Reading the output: an editor route showing `path.write_text` is usually
the FILESYSTEM PROSE BACKEND writing its own file, not a second local
write, because this pass walks into the backend. The acceptance test
tells the two apart; this one deliberately does not, since the whole
point of a wide net is that it does not decide anything.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "leaguepage"
sys.path.insert(0, str(REPO))

# No "replace": Path.replace and str.replace share a name, and every
# slug-building call in the codebase is the latter.
FS_WRITES = {"write_text", "write_bytes", "unlink", "mkdir", "rmtree",
             "rename", "touch", "copytree", "copy2", "copyfile"}
PROSE_WRITES = {"put", "delete"}
MUTATING_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def storage_mutators() -> set[str]:
    tree = ast.parse((PKG / "storage.py").read_text(encoding="utf-8"))
    out = set()
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef) or cls.name != "Storage":
            continue
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if MUTATING_SQL.search(ast.unparse(fn)):
                    out.add(fn.name)
    return out


def all_functions() -> tuple[dict, dict]:
    """Every function in the package, by bare name and by module.name."""
    by_name = defaultdict(list)
    src_of = {}
    for path in sorted(PKG.rglob("*.py")):
        mod = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                by_name[node.name].append((mod, node))
                src_of[(mod, node.name)] = node
    return by_name, src_of


def calls_in(node) -> tuple[set[str], set[tuple[str, str]]]:
    """(bare callee names, (kind, detail) sinks) for one function body,
    excluding nested function definitions, which are visited separately."""
    names: set[str] = set()
    sinks: set[tuple[str, str]] = set()
    mutators = MUTATORS
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name):
            names.add(f.id)
        elif isinstance(f, ast.Attribute):
            attr = f.attr
            names.add(attr)
            recv = ast.unparse(f.value)
            if attr in mutators and not recv.startswith(("self.", "cur", "conn")):
                sinks.add(("sqlite", attr))
            if attr in FS_WRITES:
                sinks.add(("filesystem", f"{recv}.{attr}"[:44]))
            if attr in PROSE_WRITES and (
                    "repo" in recv or "repository" in recv
                    or recv in ("target", "source", "pg", "fs")):
                sinks.add(("prose", f"{recv}.{attr}"))
        # open(path, "w")
        if isinstance(f, ast.Name) and f.id == "open":
            mode = ""
            if len(sub.args) > 1 and isinstance(sub.args[1], ast.Constant):
                mode = str(sub.args[1].value)
            if any(c in mode for c in "wax"):
                sinks.add(("filesystem", "open(mode=w)"))
    return names, sinks


def reachable_sinks(start: ast.AST, limit: int = 900):
    seen_fn: set[int] = set()
    sinks: set[tuple[str, str]] = set()
    queue = [start]
    steps = 0
    while queue and steps < limit:
        node = queue.pop()
        steps += 1
        if id(node) in seen_fn:
            continue
        seen_fn.add(id(node))
        names, s = calls_in(node)
        sinks |= s
        for nested in ast.walk(node):
            if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and nested is not node:
                queue.append(nested)
        for name in names:
            for _mod, fn in BY_NAME.get(name, [])[:4]:
                if id(fn) not in seen_fn:
                    queue.append(fn)
    return sinks


MUTATORS = storage_mutators()
BY_NAME, _SRC = all_functions()


def main() -> int:
    from leaguepage.desk import create_app

    app = create_app(db_path=REPO / "data" / "league.sqlite3")
    print(f"# Storage methods that mutate: {len(MUTATORS)}")
    rows = []
    for r in app.routes:
        methods = sorted(getattr(r, "methods", []) or [])
        if not any(m in methods for m in ("POST", "PUT", "PATCH", "DELETE")):
            continue
        name = getattr(r, "name", "?")
        candidates = BY_NAME.get(name, [])
        if not candidates:
            rows.append((r.path, name, "NO SOURCE FOUND", set()))
            continue
        sinks = set()
        for _mod, fn in candidates:
            sinks |= reachable_sinks(fn)
        rows.append((r.path, name, "", sinks))

    out = []
    for path, name, note, sinks in sorted(rows, key=lambda x: x[1]):
        sq = sorted({d for k, d in sinks if k == "sqlite"})
        fs = sorted({d for k, d in sinks if k == "filesystem"})
        pr = sorted({d for k, d in sinks if k == "prose"})
        out.append({"route": path, "name": name, "note": note,
                    "sqlite": sq, "filesystem": fs, "prose": pr})
        print(f"\n{name}  {path}")
        print(f"  sqlite    : {', '.join(sq) if sq else '-'}")
        print(f"  filesystem: {', '.join(fs) if fs else '-'}")
        print(f"  prose     : {', '.join(pr) if pr else '-'}")
    Path(sys.argv[1] if len(sys.argv) > 1 else "route_writes.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
