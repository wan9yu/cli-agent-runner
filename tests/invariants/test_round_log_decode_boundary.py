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


def test_open_round_log_should_pin_errors_replace_when_invoked() -> None:
    src = (PKG / "round_log.py").read_text(encoding="utf-8")

    fn_src = ast.get_source_segment(src, _func(ast.parse(src), "open_round_log"))
    assert fn_src is not None

    assert 'errors="replace"' in fn_src


def _reader_call_names(module_name: str, func_name: str) -> set[str]:
    src = (PKG / module_name).read_text(encoding="utf-8")
    return _call_names(_func(ast.parse(src), func_name))


def test_monitor_state_tail_reader_should_use_helper_not_bare_open_when_invoked() -> None:
    # load_round_log_tails lives in _monitor_state.py (monitor.py pure-layer split).
    module_name = "_monitor_state.py"
    func_name = "load_round_log_tails"

    actual = _reader_call_names(module_name, func_name)

    assert "open_round_log" in actual
    assert not actual & {"open", "read_text"}


def test_round_view_log_reader_should_use_helper_not_bare_read_text_when_invoked() -> None:
    module_name = "round_view.py"
    func_name = "build_round_view"

    actual = _reader_call_names(module_name, func_name)

    assert "open_round_log" in actual
    assert not actual & {"open", "read_text"}


def test_runner_network_blip_scan_should_use_helper_not_bare_read_text_when_invoked() -> None:
    module_name = "runner.py"
    func_name = "_scan_round_log_for_network_blip"

    actual = _reader_call_names(module_name, func_name)

    assert "open_round_log" in actual
    assert not actual & {"open", "read_text"}
