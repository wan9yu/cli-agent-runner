"""events-*.jsonl decode convergence (0.2.13 Group D): every reader shares
one decode policy (utf-8, lossy fallback on bad bytes) via
``events.open_events_jsonl``, and every reader skips a non-dict-shaped line
instead of handing callers something ``.get()`` chokes on -- reproduced as
live AttributeError crashes (spec-review correction) on the peek
(``monitor.parse_events_from_jsonl_files`` -> ``round_view.build_round_view``)
and HTTP-progress (``http_progress._recent_events``) surfaces, not just the
throttle scanner."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner import _throttle, monitor
from agent_runner.events import open_events_jsonl
from agent_runner.http_progress import _recent_events


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


def _write_lines(path: Path, lines: list) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_given_non_dict_json_line_when_parse_events_from_jsonl_files_then_skipped(
    tmp_path: Path,
) -> None:
    """The reader ``agent-runner peek`` uses (via round_view.build_round_view)."""
    p = tmp_path / "events-2026-08.jsonl"
    _write_lines(p, [["not", "a", "dict"], 42, {"event": "round_end", "round_num": 1}])
    parsed = monitor.parse_events_from_jsonl_files([p])
    assert parsed == [{"event": "round_end", "round_num": 1}]


def test_given_non_dict_line_when_peek_round_view_built_then_survives(tmp_path: Path) -> None:
    """End-to-end reproduction of the peek --round N crash: a non-dict line in
    events-*.jsonl must not raise AttributeError out of
    round_view.build_round_view when parse_events_from_jsonl_files feeds it."""
    from agent_runner.round_view import build_round_view

    log_dir = tmp_path
    (log_dir / "rounds").mkdir()
    (log_dir / "rounds" / "R1-2026.log").write_text("hi\n")
    p = log_dir / "events-2026-08.jsonl"
    _write_lines(
        p,
        [
            ["not", "a", "dict"],
            {"ts": "2026-08-01T00:00:00Z", "event": "round_start", "round_num": 1},
        ],
    )
    parsed = monitor.parse_events_from_jsonl_files([p])
    rv = build_round_view(log_dir, 1, parsed, want_log=False)
    assert rv is not None
    assert rv.round_num == 1


def test_given_non_dict_json_line_when_event_tail_read_then_skipped(tmp_path: Path) -> None:
    p = tmp_path / "events-2026-08.jsonl"
    _write_lines(p, [["not", "a", "dict"], {"event": "round_end", "round_num": 1}])
    tail = monitor._EventTail()
    result = tail.read([p])
    assert result == [{"event": "round_end", "round_num": 1}]


def test_given_non_dict_json_line_when_recent_events_then_skipped(tmp_path: Path) -> None:
    """The HTTP progress page's events-*.jsonl reader."""
    p = tmp_path / "events-2026-08.jsonl"
    _write_lines(p, [["not", "a", "dict"], {"event": "round_end", "round_num": 1}])
    result = _recent_events(tmp_path, max_count=20)
    assert result == [{"event": "round_end", "round_num": 1}]


def test_given_blank_and_malformed_lines_when_recent_events_then_skipped(
    tmp_path: Path,
) -> None:
    """Blank lines and undecodable JSON are skipped just like non-dict lines
    (0.2.14 Group 5: _recent_events shares events._iter_parsed_lines)."""
    p = tmp_path / "events-2026-08.jsonl"
    p.write_text(
        "\n"
        + "not valid json at all\n"
        + json.dumps(["not", "a", "dict"])
        + "\n"
        + json.dumps({"event": "round_end", "round_num": 1})
        + "\n",
        encoding="utf-8",
    )
    result = _recent_events(tmp_path, max_count=20)
    assert result == [{"event": "round_end", "round_num": 1}]


def test_given_good_file_when_recent_events_then_output_unchanged(tmp_path: Path) -> None:
    """A well-formed file's events are returned in file order, unaffected by the dedup."""
    p = tmp_path / "events-2026-08.jsonl"
    _write_lines(
        p,
        [
            {"event": "round_start", "round_num": 1},
            {"event": "round_end", "round_num": 1},
        ],
    )
    result = _recent_events(tmp_path, max_count=20)
    assert result == [
        {"event": "round_start", "round_num": 1},
        {"event": "round_end", "round_num": 1},
    ]


def test_given_non_dict_json_line_when_narrate_events_then_skipped(tmp_path: Path) -> None:
    """api._tail_events_jsonl (narrate_events / stream_events_jsonl's shared
    reader) -- a non-dict line must not reach _format_narrate_line's .get(...)."""
    from agent_runner.api import narrate_events

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    p = log_dir / "events-2026-08.jsonl"
    _write_lines(
        p,
        [
            ["not", "a", "dict"],
            {"ts": "2026-08-01T00:00:00.000Z", "event": "round_start", "round_num": 1},
        ],
    )
    line = next(narrate_events(log_dir, poll_interval_s=0.01))
    assert "round_start" in line
