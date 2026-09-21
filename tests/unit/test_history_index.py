"""examples/outer_loop/history_index.py — JSONL project, skip partial last line."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tests._test_helpers import ROOT

_EX = ROOT / "examples" / "outer_loop" / "history_index.py"


def _mod():
    spec = importlib.util.spec_from_file_location("history_index", _EX)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_index_events_should_keep_digest_and_drop_noise_when_invoked() -> None:
    hi = _mod()
    rows = hi.index_events(
        [
            {
                "event": "round_start",
                "round_num": 4,
                "config_digest": "abc123",
                "config_changed": True,
            },
            {"event": "agent_spawn", "round_num": 4},
            {"event": "goal_check", "round_num": 4, "name": "tests", "ok": False},
            {"event": "round_end", "round_num": 4},
        ]
    )
    assert [r["event"] for r in rows] == ["round_start", "goal_check", "round_end"]
    assert rows[0]["config_digest"] == "abc123"
    assert rows[1]["ok"] is False


def test_iter_complete_objects_should_skip_partial_last_line_when_invoked(tmp_path: Path) -> None:
    hi = _mod()
    path = tmp_path / "events-2026-09.jsonl"
    path.write_bytes(b'{"event":"round_end"}\n{"event":"round_start"')
    assert [e["event"] for e in hi.iter_complete_objects(path)] == ["round_end"]
