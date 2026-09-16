"""Regression: `events --tail` must drain the old file's tail on month rollover."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace

from agent_runner.cli import events_cmd


def _append(p: Path, ev: dict) -> None:
    with p.open("a") as f:
        f.write(json.dumps(ev) + "\n")


def _install_fake_listener(monkeypatch, on_wait) -> None:
    """Stub the FIFO doorbell (agent_runner._notify) so ``_tail_events``'s
    ``listener.wait(1.0)`` calls ``on_wait`` deterministically instead of
    racing a real FIFO wake."""

    class _FakeListener:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def wait(self, timeout_s, *, clock=None):
            return on_wait(timeout_s)

    monkeypatch.setattr(events_cmd._notify, "open_listener", lambda log_dir: _FakeListener())


def test_tail_should_flush_departing_month_then_read_new_month_from_zero_when_boundary_crosses(
    tmp_path, monkeypatch, capsys
):
    """Given a follow over newest_scope(2) with an offset at EOF of month M, when
    month M+1's file appears with a new line AND month M gets one more appended,
    then both the appended M line and the M+1 line are emitted, in order, no dup.
    """
    aug = tmp_path / "events-2026-08.jsonl"
    sep = tmp_path / "events-2026-09.jsonl"
    _append(aug, {"event": "round_end", "round_num": 1, "ts": "2026-08-31T23:59:59Z"})

    calls = {"n": 0}

    def fake_wait(_timeout_s):
        calls["n"] += 1
        if calls["n"] == 1:
            _append(aug, {"event": "round_end", "round_num": 2, "ts": "2026-08-31T23:59:59Z"})
            _append(sep, {"event": "round_end", "round_num": 3, "ts": "2026-09-01T00:00:01Z"})
            return True
        raise KeyboardInterrupt

    _install_fake_listener(monkeypatch, fake_wait)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    rc = events_cmd._tail_events(tmp_path, {"round_end"})
    out = capsys.readouterr().out
    emitted = [json.loads(line)["round_num"] for line in out.splitlines() if line]

    assert rc == 0
    assert emitted == [2, 3]  # August's tail drained, then September from byte 0 -- no dup
