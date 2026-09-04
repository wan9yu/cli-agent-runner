"""Invariants for _BUILTIN_KINDS single-source-of-truth via reflection."""

from __future__ import annotations

import ast

from tests.invariants._event_scan import (
    PKG,
    emit_kind_args,
    kind_constant_names,
    kind_literals,
    package_modules,
)


def test_given_builtin_kinds_when_scanned_then_each_has_a_constant_emit_site() -> None:
    """Every declared built-in kind appears as the value of some core emit call.

    Closes the "declared but never happens" hole structurally: a kind with no
    emitter is a promise to consumers that the supervisor never keeps. Resolves
    each emit's kind argument to the constant(s) it can evaluate to (see
    ``kind_constant_names`` — value positions only, not selector conditions).
    Scoped to _BUILTIN_KINDS — plugin kinds are emitted from plugin code.
    """
    from agent_runner import events

    value_by_name = {
        name: value
        for name, value in vars(events).items()
        if name.isupper() and isinstance(value, str) and value in events._BUILTIN_KINDS
    }
    emitted: set[str] = set()
    for path in package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for arg in emit_kind_args(tree):
            for name in kind_constant_names(arg):
                if name in value_by_name:
                    emitted.add(value_by_name[name])
    never = sorted(events._BUILTIN_KINDS - emitted)
    assert never == [], f"declared but never emitted: {never}"


def test_given_emit_alias_import_when_scanned_then_kind_arg_resolved() -> None:
    """monitor.py imports `emit as emit_event`; a scan that matches only
    `events.emit` is blind to it. _emit.py uses the bare `emit` name.
    """
    tree = ast.parse(
        "from agent_runner.events import emit as emit_event\n"
        "from agent_runner.events import emit\n"
        'emit_event(log_dir, "aliased_kind", x=1)\n'
        'emit(log_dir, "bare_kind", x=1)\n'
        'events.emit(log_dir, "qualified_kind", x=1)\n'
    )
    assert [a.value for a in emit_kind_args(tree)] == [
        "aliased_kind",
        "bare_kind",
        "qualified_kind",
    ]


def test_given_package_when_listed_then_scan_reaches_subpackages() -> None:
    """rglob, not glob: cli/ and builtin_plugins/ hold real emit sites."""
    names = {p.relative_to(PKG).as_posix() for p in package_modules()}

    # vacuity-guard: proves package_modules() actually finds real files (not
    # just that it's non-empty) — every other test in this file and in
    # test_event_kind_registry.py shares this same corpus source.
    assert "cli/serve_cmd.py" in names
    assert "builtin_plugins/default_dirty_handler.py" in names
    assert "events.py" not in names  # events.py defines the kinds; it is the source


def test_given_emit_calls_when_scanned_then_kind_is_a_constant_not_a_literal() -> None:
    """emit() kinds come from events.py constants. A raw literal is invisible to
    find-references and lets a declared kind go silently unemitted.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PKG.parent).as_posix()
        for arg in emit_kind_args(tree):
            offenders.extend((rel, lit.lineno, lit.value) for lit in kind_literals(arg))
    assert sorted(offenders) == [], (
        f"events.emit() called with a raw kind literal: {sorted(offenders)}"
    )


# Namespaces that legitimately spell a string the same way an event kind is
# spelled. Keyed by (module path, enclosing function).
_ALLOWED_KIND_SPELLINGS: dict[tuple[str, str], str] = {
    # Restart-action enum: Literal["config_broken", "crash_loop", "continue"].
    # "continue" has no constant, and a constant cannot sit inside Literal[...].
    ("agent_runner/_serve_policy.py", "post_round_decision"): "restart-action enum",
    # Mem-loop give-up cap's own action enum: Literal["mem_loop", "continue"].
    # Shares a spelling with events.MEM_LOOP by design (mem_loop IS the give-up
    # event kind here, unlike "continue" which has no constant) — same pattern
    # as post_round_decision's config_broken/crash_loop above.
    ("agent_runner/_serve_policy.py", "_mem_loop_decision"): "restart-action enum",
    # Exit-0 no-progress give-up cap's own action enum: Literal["stalled_no_progress",
    # "continue"] (0.2.16 Task 6). Shares a spelling with events.STALLED_NO_PROGRESS
    # by design, same pattern as _mem_loop_decision above.
    ("agent_runner/_serve_policy.py", "_no_progress_decision"): "restart-action enum",
    # Compares the post_round_decision restart-action enum, not event kinds.
    # (0.2.16 Task 5a: this comparison moved from serve_cmd.cmd into its own
    # helper in _serve_round.py so cmd() stays under its LOC budget.)
    ("agent_runner/cli/_serve_round.py", "round_outcome_exit_code"): "restart-action enum",
}


def _enclosing_function(tree: ast.Module) -> dict[int, str]:
    """Map id(node) -> outermost enclosing function name (ast.walk is breadth-first,
    so setdefault records the outermost, which is what the allow-list keys on).
    """
    out: dict[int, str] = {}
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(func):
                out.setdefault(id(node), func.name)
    return out


def _metrics_event_literal_ids(tree: ast.Module) -> set[int]:
    """id() of every literal passed as metrics.log_metrics(event=...).

    The metrics stream has its own event namespace in metrics.jsonl; it merely
    spells round_start / round_end the same way events.py does.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "log_metrics":
            for kw in node.keywords:
                if kw.arg == "event" and isinstance(kw.value, ast.Constant):
                    ids.add(id(kw.value))
    return ids


def test_given_agent_runner_source_when_scanned_then_no_raw_builtin_kind_literals() -> None:
    """Built-in kinds are referenced through events.py constants, so a kind has
    exactly one spelling in the tree and find-references reaches its readers.

    Scoped to _BUILTIN_KINDS, not KNOWN_EVENT_KINDS: plugin kinds are registered
    at runtime from external packages and are defined out of tree.
    """
    from agent_runner import events

    offenders: list[tuple[str, int, str]] = []
    for path in package_modules():
        rel = path.relative_to(PKG.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed_ids = _metrics_event_literal_ids(tree)
        enclosing = _enclosing_function(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if node.value not in events._BUILTIN_KINDS:
                continue
            if id(node) in allowed_ids:
                continue
            if (rel, enclosing.get(id(node), "")) in _ALLOWED_KIND_SPELLINGS:
                continue
            offenders.append((rel, node.lineno, node.value))
    assert sorted(offenders) == [], f"raw event-kind literals: {sorted(offenders)}"
