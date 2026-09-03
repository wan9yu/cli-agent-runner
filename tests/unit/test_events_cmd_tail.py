"""Regression: `events --tail` must emit each event exactly once."""

from __future__ import annotations

import json
import signal
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runner import events
from agent_runner.cli import events_cmd


def _events_file(log_dir: Path) -> Path:
    return log_dir / f"events-{datetime.now(UTC).strftime('%Y-%m')}.jsonl"


def _append(path: Path, kind: str, n: int, ts: str | None = None) -> None:
    payload: dict[str, object] = {"event": kind, "n": n}
    if ts is not None:
        payload = {"ts": ts, **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def test_given_event_appended_during_read_loop_when_tailing_then_each_emitted_once(
    tmp_log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A writer appending mid-loop must not cause a re-emit.

    ``for line in f`` reads to true EOF, past the size sampled before the loop;
    recording that stale size rewinds the offset and reprints the tail.
    """
    events_file = _events_file(tmp_log_dir)
    _append(events_file, "seed", 0)  # consumed by the first-poll offset sample

    polls = {"n": 0}

    def fake_sleep(_interval: float) -> None:
        polls["n"] += 1
        if polls["n"] == 1:
            _append(events_file, "round_start", 1)
        elif polls["n"] >= 4:
            raise KeyboardInterrupt

    injected = {"done": False}

    def loads_appending_midloop(s: str):
        evt = json.loads(s)
        if not injected["done"] and evt.get("event") == "round_start":
            injected["done"] = True
            _append(events_file, "round_start", 2)  # lands while the loop is running
        return evt

    # Rebind the module's own globals, never the shared stdlib modules: patching
    # json.loads / time.sleep process-wide would reach unrelated test machinery.
    # The line parse+dict-guard loop lives in agent_runner.events (shared by
    # every events-*.jsonl reader) since 0.2.13, not in events_cmd itself.
    monkeypatch.setattr(events_cmd.SYSTEM_CLOCK, "sleep", fake_sleep)
    monkeypatch.setattr(
        events,
        "json",
        SimpleNamespace(loads=loads_appending_midloop, JSONDecodeError=json.JSONDecodeError),
    )
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    assert events_cmd._tail_events(tmp_log_dir, {"round_start"}) == 0

    emitted = [json.loads(line)["n"] for line in capsys.readouterr().out.splitlines() if line]
    assert emitted == [1, 2], f"expected each event once, got {emitted}"


def test_given_non_dict_json_line_appended_when_tailing_then_skipped_not_crashed(
    tmp_log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid-JSON but non-dict line (bare list) appended mid-poll must be
    skipped by ``_emit_new_lines``, not crash the tail loop (0.2.13 Group D)."""
    events_file = _events_file(tmp_log_dir)
    _append(events_file, "seed", 0)

    polls = {"n": 0}

    def fake_sleep(_interval: float) -> None:
        polls["n"] += 1
        if polls["n"] == 1:
            with events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(["not", "a", "dict"]) + "\n")
            _append(events_file, "round_start", 1)
        elif polls["n"] >= 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(events_cmd.SYSTEM_CLOCK, "sleep", fake_sleep)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    assert events_cmd._tail_events(tmp_log_dir, {"round_start"}) == 0

    emitted = [json.loads(line)["n"] for line in capsys.readouterr().out.splitlines() if line]
    assert emitted == [1]


def test_given_since_when_tailing_then_replay_then_live_lines_each_once(
    tmp_log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--tail --since replays the backlog, then follows — with no gap, no repeat."""
    events_file = _events_file(tmp_log_dir)
    _append(events_file, "round_start", 0, ts="2026-07-01T09:00:00.000Z")  # before --since
    _append(events_file, "round_start", 1, ts="2026-07-01T10:00:00.000Z")  # replayed
    _append(events_file, "other", 2, ts="2026-07-01T11:00:00.000Z")  # wrong kind

    polls = {"n": 0}

    def fake_sleep(_interval: float) -> None:
        polls["n"] += 1
        if polls["n"] == 1:
            _append(events_file, "round_start", 3, ts="2026-07-01T12:00:00.000Z")
        elif polls["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(events_cmd.SYSTEM_CLOCK, "sleep", fake_sleep)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    since = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    assert events_cmd._tail_events(tmp_log_dir, {"round_start"}, since=since) == 0

    emitted = [json.loads(line)["n"] for line in capsys.readouterr().out.splitlines() if line]
    assert emitted == [1, 3], f"expected replay then live, each once, got {emitted}"
