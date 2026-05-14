"""Tests for api.stream_events_jsonl iterator."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Thread


def test_given_events_in_file_when_stream_then_yields_dicts(tmp_path: Path) -> None:
    """Newly-written events are yielded as parsed dicts."""
    from agent_runner.api import stream_events_jsonl

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    events_path = log_dir / "events-2026-05-14.jsonl"
    events_path.write_text("")

    def writer():
        import time

        time.sleep(0.1)
        with events_path.open("a") as f:
            evt = {"ts": "2026-05-14T12:00:00Z", "event": "round_start", "round_num": 1}
            f.write(json.dumps(evt) + "\n")
            f.flush()

    t = Thread(target=writer)
    t.start()

    it = stream_events_jsonl(log_dir, poll_interval_s=0.05)
    out = next(it)
    t.join()
    assert out["event"] == "round_start"
    assert out["round_num"] == 1


def test_given_multiple_events_when_stream_then_yields_in_order(tmp_path: Path) -> None:
    """Multiple events emitted in order are yielded in the same order."""
    from agent_runner.api import stream_events_jsonl

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    events_path = log_dir / "events-2026-05-14.jsonl"
    events_path.write_text("")

    def writer():
        import time

        time.sleep(0.05)
        with events_path.open("a") as f:
            for i in range(3):
                f.write(json.dumps({"ts": "x", "event": "round_start", "round_num": i}) + "\n")
                f.flush()
                time.sleep(0.02)

    t = Thread(target=writer)
    t.start()

    it = stream_events_jsonl(log_dir, poll_interval_s=0.02)
    seen = [next(it)["round_num"] for _ in range(3)]
    t.join()
    assert seen == [0, 1, 2]


def test_given_rotation_when_stream_then_follows_new_file(tmp_path: Path) -> None:
    """When a new date-stamped file appears, the iterator picks it up.

    Pre-existing events in the first file are historical (skipped at init).
    New events appended to the first file after init, and all events in newly
    created files, are yielded.
    """
    from agent_runner.api import stream_events_jsonl

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    first = log_dir / "events-2026-05-14.jsonl"
    first.write_text(json.dumps({"ts": "x", "event": "round_start", "round_num": 1}) + "\n")
    # First file's pre-existing event is historical; iterator should skip it.

    def writer():
        import time

        time.sleep(0.1)
        second = log_dir / "events-2026-05-15.jsonl"
        second.write_text(json.dumps({"ts": "y", "event": "round_start", "round_num": 2}) + "\n")
        time.sleep(0.05)
        # Append to FIRST file too — verify both old-file-new-event AND new-file events yield
        with first.open("a") as f:
            f.write(json.dumps({"ts": "z", "event": "round_start", "round_num": 3}) + "\n")
            f.flush()

    t = Thread(target=writer)
    t.start()

    it = stream_events_jsonl(log_dir, poll_interval_s=0.05)
    out1 = next(it)
    out2 = next(it)
    t.join()
    # Iteration order depends on internal sort + which file was poll-checked first.
    # Both 2 (new file) and 3 (new event in old file) should appear; round_num=1 must NOT.
    round_nums = sorted({out1["round_num"], out2["round_num"]})
    assert round_nums == [2, 3]
