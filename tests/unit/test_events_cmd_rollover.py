"""Regression: `events --tail` must drain the old file's tail on month rollover."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner.cli import events_cmd


def _append(p: Path, ev: dict) -> None:
    with p.open("a") as f:
        f.write(json.dumps(ev) + "\n")


def test_rollover_drains_old_file_tail_then_starts_new_at_zero(tmp_path, monkeypatch, capsys):
    aug = tmp_path / "events-2026-08.jsonl"
    sep = tmp_path / "events-2026-09.jsonl"
    _append(aug, {"event": "round_end", "round_num": 1, "ts": "2026-08-31T23:59:59Z"})

    files = iter([aug, aug, sep, sep])  # first poll seeds; then a late Aug line; then Sept
    monkeypatch.setattr(events_cmd, "_current_month_events_file", lambda _ld: next(files))

    calls = {"n": 0}

    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] == 1:
            _append(aug, {"event": "round_end", "round_num": 2, "ts": "2026-08-31T23:59:59Z"})
        elif calls["n"] == 2:
            _append(sep, {"event": "round_end", "round_num": 3, "ts": "2026-09-01T00:00:01Z"})
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(events_cmd.SYSTEM_CLOCK, "sleep", fake_sleep)

    rc = events_cmd._tail_events(tmp_path, {"round_end"})
    out = capsys.readouterr().out
    assert rc == 0
    assert '"round_num": 2' in out  # late-August line drained, not lost on rollover
    assert '"round_num": 3' in out  # September line picked up from byte 0
    assert '"round_num": 1' not in out  # pre-existing backlog still skipped (first-iter seed)
