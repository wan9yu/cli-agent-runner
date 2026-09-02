"""events-*.jsonl decode convergence (0.2.13 Group D): every reader shares
one decode policy (utf-8, lossy fallback on bad bytes) via
``events.open_events_jsonl``, and ``_throttle._iter_events`` skips a
non-dict-shaped line instead of handing callers something ``.get()`` chokes
on."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner import _throttle
from agent_runner.events import open_events_jsonl


def test_given_non_utf8_bytes_when_open_events_jsonl_then_reads_without_raising(
    tmp_path: Path,
) -> None:
    p = tmp_path / "events-2026-08.jsonl"
    p.write_bytes(b'{"event": "x"}\n\xff\xfe not valid utf-8\n')
    with open_events_jsonl(p) as f:
        lines = list(f)
    assert len(lines) == 2  # the bad byte degrades to a replacement char, not a crash


def test_given_non_utf8_bytes_in_events_file_when_iter_events_then_survives(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path
    p = log_dir / "events-2026-08.jsonl"
    p.write_bytes(b'{"event": "transient_error_detected", "agent": "claude"}\n\xff\xfe\n')
    parsed = list(_throttle._iter_events(p))
    assert any(e.get("event") == "transient_error_detected" for e in parsed)


def test_given_non_dict_json_line_when_iter_events_then_skipped(tmp_path: Path) -> None:
    p = tmp_path / "events-2026-08.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(["not", "a", "dict"]),
                json.dumps(42),
                json.dumps({"event": "agent_usage_recorded", "agent": "claude", "success": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parsed = list(_throttle._iter_events(p))
    assert parsed == [{"event": "agent_usage_recorded", "agent": "claude", "success": True}]
