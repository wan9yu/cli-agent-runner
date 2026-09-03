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


def _assert_reader_uses_helper(module_name: str, func_name: str) -> None:
    src = (PKG / module_name).read_text(encoding="utf-8")
    fn = _func(ast.parse(src), func_name)
    names = _call_names(fn)
    assert "open_round_log" in names, f"{func_name} must decode round logs via open_round_log"
    assert "open" not in names, f"{func_name} must not bare-open() a round log outside the helper"
    assert "read_text" not in names, f"{func_name} must not read_text() a round log outside helper"


def test_monitor_state_tail_reader_uses_helper_not_bare_open() -> None:
    # load_round_log_tails lives in _monitor_state.py (monitor.py pure-layer split).
    _assert_reader_uses_helper("_monitor_state.py", "load_round_log_tails")


def test_round_view_log_reader_uses_helper_not_bare_read_text() -> None:
    _assert_reader_uses_helper("round_view.py", "build_round_view")


def test_runner_network_blip_scan_uses_helper_not_bare_read_text() -> None:
    _assert_reader_uses_helper("runner.py", "_scan_round_log_for_network_blip")
