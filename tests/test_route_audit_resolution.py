"""The route audit must follow calls, not names that look alike.

THE BUG THIS EXISTS FOR

`scripts/audit_route_writes.py` walks the call graph to answer "which
stores can this route write?". Until 2026-09-09 it followed every function
in the package that SHARED a callee's bare name. There are three `render`s
in `leaguepage`:

    prose.render            renders Markdown to HTML and writes nothing
    site_build.render       a NESTED helper inside `build`, which writes
                            a page to disk
    writing_packet.render   a method

`about_preview` calls `prose.render`, so the audit reported it as writing
files. It renders Markdown and returns JSON. The behavioural audit knew
better -- `test_hosted_mutation_audit.py` drives the route and records no
write -- and the two disagreed for as long as nobody read them together.

That matters because the hosted-safety table is derived from this pass. A
diagnostic that invents a sink in a different module makes the table wrong
in the direction that looks responsible, which is the hard kind to catch.

WHAT IS PINNED

The resolution rules, and the real collision as a regression case. Also a
positive control: the fix reduced what the audit reports, so something has
to prove it did not simply stop following calls.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def audit():
    """The script, imported as a module. It parses the package at import
    time and needs no database."""
    spec = importlib.util.spec_from_file_location(
        "_audit_route_writes", REPO / "scripts" / "audit_route_writes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sinks(audit, module: str, func: str):
    """Every sink reachable from one named function."""
    found = [fn for kind, fn in audit.DEFS[(module, func)]]
    assert found, f"{module}.{func} not found"
    sinks, unresolved = set(), set()
    for fn in found:
        s, u = audit.reachable_sinks(fn, module)
        sinks |= s
        unresolved |= u
    return sinks, unresolved


# ------------------------------------------------------- the real collision

def test_the_collision_this_was_written_for_still_exists(audit):
    """If `render` ever stops being ambiguous, this file stops testing
    anything and should be pointed at whatever replaced it."""
    kinds = {kind for mod, name in audit.DEFS
             for kind, _fn in audit.DEFS[(mod, name)]
             if name == "render"}
    modules = {mod for (mod, name) in audit.DEFS if name == "render"}
    assert len(modules) >= 2, f"`render` is no longer ambiguous: {modules}"
    assert "prose" in modules and "site_build" in modules
    assert kinds & {"nested"}, "site_build.render was the nested one"


def test_a_nested_helper_is_not_reachable_by_sharing_a_name(audit):
    """The narrow statement of the bug: `site_build`'s inner `render`
    writes pages, and nothing outside `build` can call it."""
    assert "render" in audit.ANY_NAME
    reachable = {mod for mod, _fn in audit.BY_NAME.get("render", [])}
    assert "site_build" not in reachable, (
        "a nested function is in the name index that call resolution "
        "searches; that is exactly how about_preview grew a file write")
    assert "site_build" in {mod for mod, _fn in audit.ANY_NAME["render"]}, (
        "the seeding index should still see it")


def test_rendering_markdown_reaches_no_sink(audit):
    """`prose.render` is the callee `about_preview` actually has."""
    sinks, unresolved = _sinks(audit, "prose", "render")
    assert sinks == set(), f"prose.render should write nothing, got {sinks}"
    assert unresolved == set()


def test_a_module_alias_resolves_to_that_module_and_no_other(audit):
    """`from leaguepage import prose` then `prose.render(...)`: one target."""
    aliases = audit.ALIASES["desk_site"]
    assert aliases.get("prose") == "prose"
    tree = ast.parse((REPO / "leaguepage" / "desk_site.py")
                     .read_text(encoding="utf-8"))
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "render")
    holder = ast.FunctionDef(
        name="_probe", args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[ast.Expr(call)], decorator_list=[], returns=None,
        type_params=[], lineno=1, col_offset=0)
    targets, unresolved, _ = audit.calls_in(holder, "desk_site")
    assert ("prose", "render") in targets
    assert not any(m == "site_build" for m, _n in targets)


def test_a_method_call_still_follows_every_backend(audit):
    """The wide net is the point: `act.state.set_site_document` could be
    either editorial state, and both must be walked."""
    defs = audit.DEFS[("editorial_state", "set_site_document")]
    assert {kind for kind, _fn in defs} == {"method"}
    # Three, not two: the Protocol stub is a method too, and walking it
    # costs nothing because its body is `...`.
    assert len(defs) == 3, (
        "one method per backend plus the protocol stub; if this changes, "
        "so does what the audit is proving")


# ------------------------------------------------------- positive controls

def test_the_filesystem_about_write_is_still_seen(audit):
    """The fix must not have simply stopped following calls. Saving About
    on the filesystem writes a file, and the audit has to say so."""
    sinks, _ = _sinks(audit, "editorial_state", "set_site_document")
    assert any(kind == "filesystem" for kind, _d in sinks), (
        f"the filesystem backend's About write disappeared: {sinks}")


def test_a_storage_mutator_is_still_seen(audit):
    """Through the route, which is where the audit reads it.

    Not through `SqliteEditorialState.add_take`: that calls
    `self._s.add_take`, and the sink rule has always skipped `self.`
    receivers so a Storage method does not report itself.
    """
    sinks, _ = _sinks(audit, "desk_editor", "track_take")
    assert ("sqlite", "add_take") in sinks, f"got {sorted(sinks)}"


def test_nothing_is_silently_dropped(audit):
    """Whatever cannot be resolved is REPORTED. A pass that quietly stops
    following calls under-reports, which is worse than over-reporting."""
    _sinks_, unresolved = _sinks(audit, "desk_site", "read_about")
    assert isinstance(unresolved, set)
    src = (REPO / "scripts" / "audit_route_writes.py").read_text(
        encoding="utf-8")
    assert "unresolved" in src and 'print(f"  unresolved:' in src
