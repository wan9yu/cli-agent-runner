"""Invariants for _BUILTIN_KINDS single-source-of-truth via reflection."""

from __future__ import annotations

import ast

from tests.invariants._event_scan import PKG, emit_kind_args, kind_literals, package_modules


def test_every_string_constant_is_a_builtin_kind():
    """Every UPPER_CASE = snake_case_value constant must be in _BUILTIN_KINDS.
    Prevents: define constant, forget to register.
    """
    import agent_runner.events as ev

    string_constants = {
        v
        for k, v in vars(ev).items()
        if k.isupper()
        and isinstance(v, str)
        and v.islower()
        and not v.startswith("_")
        and v.replace("_", "").isalnum()
    }
    missing = string_constants - ev._BUILTIN_KINDS
    assert not missing, f"constants not registered: {missing}"


def test_every_builtin_kind_has_a_constant():
    """Every entry in _BUILTIN_KINDS must correspond to a module-level constant.
    Prevents: hand-add string to set without constant; orphan stale strings.
    """
    import agent_runner.events as ev

    constant_values = {
        v
        for k, v in vars(ev).items()
        if k.isupper()
        and isinstance(v, str)
        and v.islower()
        and not v.startswith("_")
        and v.replace("_", "").isalnum()
    }
    orphans = ev._BUILTIN_KINDS - constant_values
    assert not orphans, f"_BUILTIN_KINDS members without constants: {orphans}"


def test_given_retired_sigterm_kind_when_looked_up_then_not_registered() -> None:
    """0.2.2 retires sigterm_received: nothing ever emitted it, and serve's
    graceful-stop handler — not a signal-handler emit — is the real mechanism.

    Transitional guard: superseded by the declared-then-emitted invariant, which
    makes re-adding an unemitted kind structurally impossible.
    """
    from agent_runner import events

    assert "sigterm_received" not in events._BUILTIN_KINDS


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
