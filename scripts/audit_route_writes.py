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

HOW CALLS ARE RESOLVED, AND WHY IT IS NOT BY NAME

Until 2026-09-09 this pass followed every function in the package that
SHARED A CALLEE'S NAME. `about_preview` renders Markdown and writes
nothing, and it was reported as writing files, because it calls
`prose.render` and `site_build` has a nested helper also called `render`
that writes pages. A wide net may over-report what a route reaches; it
may not invent a sink in a different module.

So a call is resolved before it is followed:

  prose.render()      the module alias is read from this module's imports,
                      so it resolves to leaguepage/prose.py and nothing else
  render()            a bare name resolves to this module's own definition,
                      then to a `from ... import`, then -- only if the name
                      is unique in the whole package -- to that
  obj.method()        the receiver's type is not knowable, so every METHOD
                      of that name is followed. Never a module-level or
                      nested function that merely shares it.

Anything left over is reported per route on an `unresolved:` line rather
than being dropped, because a pass that quietly stops following calls
under-reports, which is the failure that matters here.
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


def _kind_of(tree) -> dict:
    """Every function in one module, tagged by how it can be reached.

    "module"  a module-level def: reachable as `mod.name` or, if imported,
              as a bare name.
    "method"  a def inside a class: reachable as `something.name`, and the
              receiver's type is not knowable from the AST.
    "nested"  a def inside another def. NOT reachable by name from anywhere
              else, which is the whole point of recording it -- see the
              collision this pass used to have, in the docstring below.
    """
    out = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[id(node)] = "module"
        elif isinstance(node, ast.ClassDef):
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[id(sub)] = "method"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(id(node), "nested")
    return out


def _imports_of(tree) -> tuple[dict, dict]:
    """(module aliases, from-imported names) for one module.

    Read from the whole tree, not just the top level, because this package
    imports inside functions on purpose to break cycles.

      from leaguepage import prose           ->  alias  prose -> "prose"
      from leaguepage import auth as a       ->  alias  a     -> "auth"
      import leaguepage.issue_builder as ib  ->  alias  ib    -> "issue_builder"
      from leaguepage.publish import render  ->  name   render -> ("publish",
                                                                   "render")
    """
    aliases, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("leaguepage."):
                    aliases[a.asname or a.name.split(".")[-1]] = \
                        a.name.split(".")[-1]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "leaguepage":
                for a in node.names:
                    aliases[a.asname or a.name] = a.name
            elif mod.startswith("leaguepage."):
                stem = mod.split(".")[-1]
                for a in node.names:
                    names[a.asname or a.name] = (stem, a.name)
    return aliases, names


def all_functions() -> tuple[dict, dict, dict, dict, dict]:
    """The package indexed four ways, so a call can be resolved rather than
    guessed at.

      by_name   bare name -> [(mod, fn)], MODULE-LEVEL AND METHODS ONLY.
                This is what call resolution searches, so a nested helper
                can never be reached by sharing a name.
      any_name  bare name -> [(mod, fn)], INCLUDING nested. Used only to
                FIND a route handler, which is itself nested inside
                `register_*` -- seeding the walk is not resolving a call.
      defs      (mod, name) -> [fn]           (a module may define a name
                                               more than once: two backend
                                               classes, one method name)
      aliases   mod -> {alias: module_stem}
      imported  mod -> {name: (module_stem, name)}
    """
    by_name = defaultdict(list)
    any_name = defaultdict(list)
    defs = defaultdict(list)
    aliases, imported = {}, {}
    for path in sorted(PKG.rglob("*.py")):
        mod = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        kinds = _kind_of(tree)
        aliases[mod], imported[mod] = _imports_of(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            kind = kinds.get(id(node), "nested")
            defs[(mod, node.name)].append((kind, node))
            any_name[node.name].append((mod, node))
            if kind != "nested":
                by_name[node.name].append((mod, node))
    return by_name, any_name, defs, aliases, imported


def calls_in(node, mod: str) -> tuple[set, set, set]:
    """(resolved targets, unresolved call names, sinks) for one function.

    A target is `(mod, name)`. Nested definitions are visited separately by
    the caller, so they are not followed from here.
    """
    targets: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    sinks: set[tuple[str, str]] = set()
    mutators = MUTATORS
    mod_aliases = ALIASES.get(mod, {})
    mod_imported = IMPORTED.get(mod, {})

    def resolve_bare(name: str) -> None:
        if (mod, name) in DEFS:
            targets.add((mod, name))
        elif name in mod_imported:
            targets.add(mod_imported[name])
        elif len(BY_NAME.get(name, [])) == 1:
            targets.add((BY_NAME[name][0][0], name))
        elif BY_NAME.get(name):
            unresolved.add(name)

    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name):
            resolve_bare(f.id)
        elif isinstance(f, ast.Attribute):
            attr = f.attr
            recv = ast.unparse(f.value)
            root = recv.split(".")[0].split("(")[0]
            if root in mod_aliases and (mod_aliases[root], attr) in DEFS:
                # `prose.render(...)` is prose.py's render and nothing else.
                targets.add((mod_aliases[root], attr))
            else:
                # A method on an object whose type the AST does not know.
                # Follow every METHOD of that name -- the wide net is the
                # point -- but never a module-level or nested function that
                # merely shares it.
                hits = [(m, fn) for m, fn in BY_NAME.get(attr, [])
                        if any(k == "method" for k, f2 in DEFS[(m, attr)]
                               if f2 is fn)]
                if hits:
                    targets.update((m, attr) for m, _fn in hits)
                elif BY_NAME.get(attr):
                    unresolved.add(f"{root}.{attr}")

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
    return targets, unresolved, sinks


def reachable_sinks(start, mod: str, limit: int = 4000):
    """Sinks reachable from one function, and the calls that could not be
    resolved on the way. Both are returned: a pass that silently drops what
    it could not follow is a pass that under-reports."""
    seen_fn: set[int] = set()
    sinks: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    queue = [(mod, start)]
    steps = 0
    while queue and steps < limit:
        where, node = queue.pop()
        steps += 1
        if id(node) in seen_fn:
            continue
        seen_fn.add(id(node))
        targets, un, s = calls_in(node, where)
        sinks |= s
        unresolved |= un
        for nested in ast.walk(node):
            if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and nested is not node:
                queue.append((where, nested))
        for tmod, tname in targets:
            for _kind, fn in DEFS.get((tmod, tname), []):
                if id(fn) not in seen_fn:
                    queue.append((tmod, fn))
    return sinks, unresolved


MUTATORS = storage_mutators()
BY_NAME, ANY_NAME, DEFS, ALIASES, IMPORTED = all_functions()


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
        candidates = ANY_NAME.get(name, [])
        if not candidates:
            rows.append((r.path, name, "NO SOURCE FOUND", set(), set()))
            continue
        sinks, unresolved = set(), set()
        for cmod, fn in candidates:
            s, u = reachable_sinks(fn, cmod)
            sinks |= s
            unresolved |= u
        rows.append((r.path, name, "", sinks, unresolved))

    out = []
    for path, name, note, sinks, unresolved in sorted(rows, key=lambda x: x[1]):
        sq = sorted({d for k, d in sinks if k == "sqlite"})
        fs = sorted({d for k, d in sinks if k == "filesystem"})
        pr = sorted({d for k, d in sinks if k == "prose"})
        un = sorted(unresolved)
        out.append({"route": path, "name": name, "note": note,
                    "sqlite": sq, "filesystem": fs, "prose": pr,
                    "unresolved": un})
        print(f"\n{name}  {path}")
        print(f"  sqlite    : {', '.join(sq) if sq else '-'}")
        print(f"  filesystem: {', '.join(fs) if fs else '-'}")
        print(f"  prose     : {', '.join(pr) if pr else '-'}")
        if un:
            print(f"  unresolved: {', '.join(un)}")
    Path(sys.argv[1] if len(sys.argv) > 1 else "route_writes.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
