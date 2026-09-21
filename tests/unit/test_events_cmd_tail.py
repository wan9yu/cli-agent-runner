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


def _install_fake_listener(monkeypatch: pytest.MonkeyPatch, on_wait) -> None:
    """Stub the FIFO doorbell (agent_runner._notify) so ``_tail_events``'s
    ``listener.wait(1.0)`` calls ``on_wait`` deterministically instead of
    racing a real FIFO wake -- the same seam ``SYSTEM_CLOCK.sleep`` used to
    be, before ``_tail_events`` moved onto the doorbell."""

    class _FakeListener:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def wait(self, timeout_s, *, clock=None):
            return on_wait(timeout_s)

    monkeypatch.setattr(events_cmd._notify, "open_listener", lambda log_dir: _FakeListener())


def test_event_appended_during_read_loop_should_be_emitted_once_when_tailing(
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

    def fake_wait(_timeout_s: float) -> bool:
        polls["n"] += 1
        if polls["n"] == 1:
            _append(events_file, "round_start", 1)
        elif polls["n"] >= 4:
            raise KeyboardInterrupt
        return True

    injected = {"done": False}

    def loads_appending_midloop(s: str):
        evt = json.loads(s)
        if not injected["done"] and evt.get("event") == "round_start":
            injected["done"] = True
            _append(events_file, "round_start", 2)  # lands while the loop is running
        return evt

    # Rebind the module's own globals, never the shared stdlib modules: patching
    # json.loads process-wide would reach unrelated test machinery. The line
    # parse+dict-guard loop lives in agent_runner.events (shared by every
    # events-*.jsonl reader) since 0.2.13, not in events_cmd itself.
    _install_fake_listener(monkeypatch, fake_wait)
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


def test_non_dict_json_line_appended_should_be_skipped_without_crashing_when_tailing(
    tmp_log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid-JSON but non-dict line (bare list) appended mid-poll must be
    skipped by the tail loop (via ``event_log`` / ``_iter_parsed_lines``), not
    crash it (0.2.13 Group D)."""
    events_file = _events_file(tmp_log_dir)
    _append(events_file, "seed", 0)

    polls = {"n": 0}

    def fake_wait(_timeout_s: float) -> bool:
        polls["n"] += 1
        if polls["n"] == 1:
            with events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(["not", "a", "dict"]) + "\n")
            _append(events_file, "round_start", 1)
        elif polls["n"] >= 4:
            raise KeyboardInterrupt
        return True

    _install_fake_listener(monkeypatch, fake_wait)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    assert events_cmd._tail_events(tmp_log_dir, {"round_start"}) == 0

    emitted = [json.loads(line)["n"] for line in capsys.readouterr().out.splitlines() if line]
    assert emitted == [1]


def test_since_should_replay_backlog_then_live_lines_each_once_when_tailing(
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

    def fake_wait(_timeout_s: float) -> bool:
        polls["n"] += 1
        if polls["n"] == 1:
            _append(events_file, "round_start", 3, ts="2026-07-01T12:00:00.000Z")
        elif polls["n"] >= 3:
            raise KeyboardInterrupt
        return True

    _install_fake_listener(monkeypatch, fake_wait)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    since = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    assert events_cmd._tail_events(tmp_log_dir, {"round_start"}, since=since) == 0

    emitted = [json.loads(line)["n"] for line in capsys.readouterr().out.splitlines() if line]
    assert emitted == [1, 3], f"expected replay then live, each once, got {emitted}"


def test_tail_should_print_original_line_bytes_when_keys_unsorted_and_nonascii(
    tmp_path, capsys, monkeypatch
):
    """The tail streamer must print the ORIGINAL line bytes (remote_relay pipes
    its stdout) -- never a re-serialized dict, which would reorder keys and
    re-encode non-ASCII."""
    log_dir = tmp_path
    events_file = log_dir / "events-2026-09.jsonl"
    line = '{"event": "round_start", "z": 1, "a": "é 中"}'

    calls = {"n": 0}

    def fake_wait(_timeout_s: float) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            with events_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        raise KeyboardInterrupt

    _install_fake_listener(monkeypatch, fake_wait)
    monkeypatch.setattr(
        events_cmd,
        "signal",
        SimpleNamespace(SIGINT=signal.SIGINT, signal=lambda *_a: None),
    )

    rc = events_cmd._tail_events(log_dir, {"round_start"})

    out = capsys.readouterr().out
    assert rc == 0
    assert line in out.splitlines()


def test_replay_since_should_seed_current_file_at_eof_when_since_names_a_future_month(tmp_path):
    (tmp_path / "events-2026-09.jsonl").write_text(
        '{"event": "old", "ts": "2026-09-01T00:00:00Z"}\n'
    )
    seed = {}
    from agent_runner import event_log
    from tests._clock import FakeClock

    list(
        event_log.replay_since(
            tmp_path, "2026-12", seed_out=seed, clock=FakeClock(start="2026-09-16T00:00:00Z")
        )
    )

    sep = tmp_path / "events-2026-09.jsonl"
    assert seed[sep] == sep.stat().st_size


def test_since_seed_should_leave_prior_month_at_eof_when_since_is_in_current_month(tmp_path):
    """CRITICAL regression: --since in the current month must NOT dump the prior month."""
    from agent_runner import event_log

    aug = tmp_path / "events-2026-08.jsonl"
    sep = tmp_path / "events-2026-09.jsonl"
    aug.write_text('{"event": "round_start", "ts": "2026-08-01T00:00:00Z"}\n')
    sep.write_text('{"event": "round_start", "ts": "2026-09-20T00:00:00Z"}\n')
    seed = {p: p.stat().st_size for p in event_log.newest_month_files(tmp_path, 2)}

    replayed = [line for line, _ in event_log.replay_since(tmp_path, "2026-09", seed_out=seed)]

    assert all("2026-08" not in line for line in replayed)
    assert seed[aug] == aug.stat().st_size


def test_matches_since_should_exclude_same_month_events_before_the_since_timestamp_when_invoked():
    from datetime import UTC, datetime

    from agent_runner.cli.events_cmd import _matches_since

    early = '{"event": "round_start", "ts": "2026-09-01T00:00:00Z"}'
    since = datetime(2026, 9, 15, tzinfo=UTC)

    assert _matches_since(early, {"round_start"}, since) is False
