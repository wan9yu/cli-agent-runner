from __future__ import annotations

import json
from pathlib import Path

from agent_runner.monitor import _EventTail


def test_second_read_returns_only_appended_lines_but_buffer_accumulates(tmp_path: Path) -> None:
    f = tmp_path / "events-2026-08.jsonl"
    f.write_text(json.dumps({"event": "round_start", "round_num": 1}) + "\n")
    tail = _EventTail()
    first = tail.read([f])
    assert len(first) == 1
    with f.open("a") as fh:
        fh.write(json.dumps({"event": "round_end", "round_num": 1}) + "\n")
    second = tail.read([f])
    # buffer carries both; the file was NOT re-read from byte 0 (offset advanced)
    assert [e["event"] for e in second] == ["round_start", "round_end"]
    assert tail.offsets[f] == f.stat().st_size
