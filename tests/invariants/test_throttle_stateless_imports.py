"""Invariant: `_throttle.py`'s detector family stays on `event_log`'s stateless
`scan` path, NEVER the offset-carrying `follow`/`read_new`/`read_new_raw` path.

A `detected` event can be thousands of lines back, so an offset cursor would
silently skip (and forget) a live throttle -- see `_tail_events`'s docstring.
C imports the MODULE handle (`from agent_runner import event_log`) and calls
`event_log.scan(...)`, so the guard inspects ATTRIBUTE access on `event_log`,
not just from-imports -- else it would pass vacuously and guard nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

_THROTTLE = Path(__file__).resolve().parents[2] / "agent_runner" / "_throttle.py"
_FORBIDDEN = {"follow", "read_new", "read_new_raw"}


def test_throttle_should_not_reach_the_offset_follow_path_when_scanned():
    tree = ast.parse(_THROTTLE.read_text(encoding="utf-8"))
    reached = set()
    for node in ast.walk(tree):
        given_attr_on_module = (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "event_log"
        )
        if given_attr_on_module and node.attr in _FORBIDDEN:
            reached.add(node.attr)
        if isinstance(node, ast.ImportFrom) and node.module == "agent_runner.event_log":
            reached |= {a.name for a in node.names} & _FORBIDDEN

    assert not reached, f"_throttle reached the offset/follow path: {reached}"


def test_throttle_should_actually_use_the_event_log_scan_path_when_scanned():
    src = _THROTTLE.read_text(encoding="utf-8")

    assert "event_log.scan" in src and "event_log.newest_month_files" in src
