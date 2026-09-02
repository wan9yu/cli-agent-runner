"""Tests for agent-runner events verb (0.1.34+)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _write_events(log_dir: Path, events: list[dict]) -> Path:
    """Write events to current-month events.jsonl, return path."""
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    path = log_dir / f"events-{month}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


def _current_month() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m")


def _write_month(log_dir: Path, month: str, events: list[dict]) -> Path:
    """Write events to events-<month>.jsonl, return path."""
    path = log_dir / f"events-{month}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


def _make_args(
    kind: str,
    *,
    window: int = -1,
    tail: bool = False,
    since: str | None = None,
    log_dir: Path | None = None,
):
    """Construct a fake argparse Namespace for cmd_events.

    window=-1 is _WINDOW_DEFAULT_SENTINEL (not explicitly set by user).
    """
    return SimpleNamespace(
        kind=kind, window=window, tail=tail, since=since, config=None, _log_dir_override=log_dir
    )


def test_events_query_one_shot_single_kind(tmp_path, capsys):
    """One-shot mode: --kind X returns last N events of X as JSON lines."""
    from agent_runner.cli import events_cmd

    _write_events(
        tmp_path,
        [
            {"event": "round_start", "round_num": 1},
            {"event": "agent_usage_recorded", "round_num": 1, "cost_usd": 0.01},
            {"event": "agent_usage_recorded", "round_num": 2, "cost_usd": 0.02},
            {"event": "round_end", "round_num": 2},
        ],
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="agent_usage_recorded", window=10)
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert json.loads(out[0])["round_num"] == 1
    assert json.loads(out[1])["round_num"] == 2


def test_events_query_multi_kind_or_filter(tmp_path, capsys):
    """--kind X,Y returns events matching either kind (OR filter), in file order."""
    from agent_runner.cli import events_cmd

    _write_events(
        tmp_path,
        [
            {"event": "round_start", "round_num": 1},
            {"event": "hook_failed", "round_num": 1, "hook_name": "x"},
            {"event": "round_end", "round_num": 1},
            {"event": "round_start", "round_num": 2},
        ],
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_start,hook_failed", window=10)
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    kinds = [json.loads(line)["event"] for line in out]
    assert kinds == ["round_start", "hook_failed", "round_start"]


def test_events_query_no_match_returns_empty(tmp_path, capsys):
    """Filter matching nothing prints nothing, exits 0."""
    from agent_runner.cli import events_cmd

    _write_events(
        tmp_path,
        [
            {"event": "round_start", "round_num": 1},
        ],
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="nonexistent_kind")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    assert capsys.readouterr().out == ""


def test_events_query_skips_non_dict_json_line(tmp_path, capsys):
    """A valid-JSON but non-dict line (bare list/number) must be skipped, not crash
    (0.2.13 Group D: every events-*.jsonl reader assumes .get(...) shape)."""
    from agent_runner.cli import events_cmd

    path = tmp_path / f"events-{_current_month()}.jsonl"
    path.write_text(
        json.dumps(["not", "a", "dict"])
        + "\n"
        + json.dumps(42)
        + "\n"
        + json.dumps({"event": "round_end", "round_num": 1})
        + "\n",
        encoding="utf-8",
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", window=10)
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["round_num"] for line in out] == [1]


def test_events_since_skips_non_dict_json_line(tmp_path, capsys):
    """Same as above but through the --since replay path (_matches_since)."""
    from agent_runner.cli import events_cmd

    path = tmp_path / f"events-{_current_month()}.jsonl"
    path.write_text(
        json.dumps(["not", "a", "dict"])
        + "\n"
        + json.dumps({"ts": "2026-07-01T10:00:00.000Z", "event": "round_end", "round_num": 1})
        + "\n",
        encoding="utf-8",
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="2026-07-01T00:00:00.000Z")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["round_num"] for line in out] == [1]


def test_events_window_and_tail_mutually_exclusive(tmp_path, capsys):
    """--window with --tail explicitly should fail with exit 2."""
    from agent_runner.cli import events_cmd

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = SimpleNamespace(
            kind="x",
            window=5,
            tail=True,
            config=None,
            _window_explicit=True,
        )
        rc = events_cmd.cmd_events(args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err.lower() or "cannot combine" in err.lower()


def test_events_since_replays_all_matches_unwindowed(tmp_path, capsys):
    """--since emits every match at or after the timestamp — not the last N."""
    from agent_runner.cli import events_cmd

    _write_events(
        tmp_path,
        [{"ts": "2026-07-01T00:00:00.000Z", "event": "round_end", "round_num": 0}]
        + [
            {"ts": f"2026-07-02T00:00:{n:02d}.000Z", "event": "round_end", "round_num": n}
            for n in range(1, 13)
        ],
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="2026-07-02T00:00:00.000Z")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    # 12 matches after `since` — the default window of 10 must not apply.
    assert [json.loads(line)["round_num"] for line in out] == list(range(1, 13))


def test_events_since_boundary_is_inclusive(tmp_path, capsys):
    """An event whose ts equals --since exactly is emitted (at-least-once)."""
    from agent_runner.cli import events_cmd

    _write_events(
        tmp_path,
        [
            {"ts": "2026-07-01T09:59:59.999Z", "event": "round_end", "round_num": 1},
            {"ts": "2026-07-01T10:00:00.000Z", "event": "round_end", "round_num": 2},
            {"ts": "2026-07-01T10:00:00.001Z", "event": "round_end", "round_num": 3},
        ],
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="2026-07-01T10:00:00.000Z")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["round_num"] for line in out] == [2, 3]


def test_events_since_replays_across_month_files(tmp_path, capsys, monkeypatch):
    """Replay spans every month file at or after `since`'s month; older ones
    are never opened."""
    from agent_runner.cli import events_cmd

    _write_month(
        tmp_path,
        "2026-01",
        [{"ts": "2026-01-15T00:00:00.000Z", "event": "round_end", "round_num": 1}],
    )
    _write_month(
        tmp_path,
        "2026-02",
        [
            {"ts": "2026-02-01T00:00:00.000Z", "event": "round_end", "round_num": 2},
            {"ts": "2026-02-20T00:00:00.000Z", "event": "round_end", "round_num": 3},
        ],
    )
    _write_month(
        tmp_path,
        "2026-03",
        [{"ts": "2026-03-01T00:00:00.000Z", "event": "round_end", "round_num": 4}],
    )

    opened: list[str] = []
    real_open = Path.open

    def spy_open(self, *a, **kw):
        opened.append(self.name)
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", spy_open)

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="2026-02-10T00:00:00.000Z")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["round_num"] for line in out] == [3, 4]
    assert "events-2026-01.jsonl" not in opened


def test_events_since_skips_malformed_and_ts_less_lines(tmp_path, capsys):
    """Unparseable lines and lines without a ts are skipped silently."""
    from agent_runner.cli import events_cmd

    path = tmp_path / f"events-{_current_month()}.jsonl"
    path.write_text(
        "not json at all\n"
        + json.dumps({"event": "round_end", "round_num": 1})
        + "\n"
        + json.dumps({"ts": "nonsense", "event": "round_end", "round_num": 2})
        + "\n"
        + json.dumps({"ts": "2026-07-01T10:00:00.000Z", "event": "round_end", "round_num": 3})
        + "\n",
        encoding="utf-8",
    )

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="2026-07-01T00:00:00.000Z")
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["round_num"] for line in out] == [3]


def test_events_since_and_window_mutually_exclusive(tmp_path, capsys):
    """--since with an explicit --window fails with exit 2."""
    from agent_runner.cli import events_cmd

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(
            kind="round_end", window=5, since="2026-07-01T10:00:00.000Z", log_dir=tmp_path
        )
        rc = events_cmd.cmd_events(args)

    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err.lower()


def test_events_since_invalid_timestamp_exits_2(tmp_path, capsys):
    """A --since value that is not ISO-8601 fails with exit 2 and one stderr line."""
    from agent_runner.cli import events_cmd

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="round_end", since="yesterday-ish", log_dir=tmp_path)
        rc = events_cmd.cmd_events(args)

    assert rc == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "--since" in err[0]


def test_events_tail_emits_new_events_as_they_arrive(tmp_path, capsys, monkeypatch):
    """--tail mode polls and emits new matching lines. Uses signal to stop after 3s."""
    import threading

    from agent_runner.cli import events_cmd

    events_file = _write_events(
        tmp_path,
        [
            {"event": "round_start", "round_num": 1},  # baseline (not in --kind filter)
        ],
    )

    # Append after a delay
    def appender():
        time.sleep(1.0)
        with events_file.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event": "anomaly_repetitive_tool", "round_num": 5, "tool_name": "Edit"}
                )
                + "\n"
            )
            f.flush()

    t = threading.Thread(target=appender, daemon=True)
    t.start()

    # Wrap the real tail loop with a 3s timeout via SIGALRM
    real_tail = events_cmd._tail_events

    def short_tail(log_dir, kind_set, since=None):
        import signal

        def timeout_handler(_signum, _frame):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(3)
        try:
            return real_tail(log_dir, kind_set, since=since)
        except KeyboardInterrupt:
            return 0
        finally:
            signal.alarm(0)

    monkeypatch.setattr(events_cmd, "_tail_events", short_tail)

    with patch.object(events_cmd, "_resolve_log_dir", return_value=tmp_path):
        args = _make_args(kind="anomaly_repetitive_tool", tail=True)
        rc = events_cmd.cmd_events(args)

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) >= 1
    assert json.loads(out[0])["event"] == "anomaly_repetitive_tool"
    assert json.loads(out[0])["round_num"] == 5
