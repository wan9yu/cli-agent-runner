from __future__ import annotations

import ast

from tests.invariants._event_scan import PKG


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _call_names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            f = c.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def test_open_round_log_pins_errors_replace() -> None:
    src = (PKG / "round_log.py").read_text(encoding="utf-8")
    fn_src = ast.get_source_segment(src, _func(ast.parse(src), "open_round_log"))
    assert 'errors="replace"' in fn_src


def test_monitor_tail_reader_uses_helper_not_bare_open() -> None:
    src = (PKG / "monitor.py").read_text(encoding="utf-8")
    fn = _func(ast.parse(src), "load_round_log_tails")
    names = _call_names(fn)
    assert "open_round_log" in names, "round-log tail must decode via open_round_log"
    assert "open" not in names, "no bare text-mode open() of a round log outside the helper"
