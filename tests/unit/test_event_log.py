"""event_log.py — the single owner of the events-*.jsonl layout + read-side
offset/tail machinery. See docs/internal/specs/2026-09-16-event-log-consolidation-design.md."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner import event_log
from agent_runner._notify import NULL_LISTENER
from agent_runner.event_log import read_new
from tests._clock import FakeClock


def test_current_month_file_should_name_the_clock_month_when_derived(tmp_path):
    clock = FakeClock(start="2026-09-16T00:00:00Z")

    got = event_log.current_month_file(tmp_path, clock=clock)

    assert got == tmp_path / "events-2026-09.jsonl"


def test_newest_month_files_should_return_newest_n_oldest_first_when_multiple_months(tmp_path):
    for m in ("2026-07", "2026-08", "2026-09"):
        (tmp_path / f"events-{m}.jsonl").write_text("")

    got = event_log.newest_month_files(tmp_path, 2)

    assert got == [tmp_path / "events-2026-08.jsonl", tmp_path / "events-2026-09.jsonl"]


def test_read_new_raw_should_preserve_original_line_bytes_when_keys_unsorted_nonascii(tmp_path):
    p = tmp_path / "events-2026-09.jsonl"
    line = '{"event": "x", "z": 1, "a": "é 中"}'
    p.write_text(line + "\n", encoding="utf-8")

    records, offsets = event_log.read_new_raw([p], {})

    assert records[0][0] == line
    assert records[0][1]["a"] == "é 中"
    assert offsets[p] == p.stat().st_size


def test_read_new_raw_should_reset_to_zero_when_file_truncated(tmp_path):
    # The rewrite MUST be shorter than the recorded offset so size < pos (the
    # truncation path). Two lines then one shorter line -- mirrors the correct
    # existing pattern at tests/unit/test_events.py:343.
    p = tmp_path / "events-2026-09.jsonl"
    p.write_text('{"event": "a"}\n{"event": "b"}\n', encoding="utf-8")
    _, offsets = event_log.read_new_raw([p], {})
    p.write_text('{"event": "c"}\n', encoding="utf-8")

    records, _ = event_log.read_new_raw([p], offsets)

    assert [d["event"] for _, d in records] == ["c"]


def test_scan_should_yield_oldest_to_newest_across_months_when_scoped(tmp_path):
    (tmp_path / "events-2026-08.jsonl").write_text('{"event": "old"}\n')
    (tmp_path / "events-2026-09.jsonl").write_text('{"event": "new"}\n')

    got = [d["event"] for d in event_log.scan(tmp_path, event_log.all_month_files)]

    assert got == ["old", "new"]


def test_newest_month_files_should_be_an_ascending_suffix_of_all_month_files_when_invoked(tmp_path):
    for m in ("2026-07", "2026-08", "2026-09"):
        (tmp_path / f"events-{m}.jsonl").write_text("")

    both = event_log.all_month_files(tmp_path)

    assert event_log.newest_month_files(tmp_path, 2) == both[-2:]


def test_follow_should_yield_only_new_records_then_wait_when_start_offset_is_eof(tmp_path):
    p = tmp_path / "events-2026-09.jsonl"
    p.write_text('{"event": "before"}\n')
    seed = {p: p.stat().st_size}
    it = event_log.follow(
        tmp_path, event_log.all_month_files, wake=NULL_LISTENER, timeout_s=0.01, offsets=seed
    )

    with open(p, "a") as f:
        f.write('{"event": "after"}\n')
    line, obj = next(it)

    assert obj["event"] == "after"
    assert line == '{"event": "after"}'


def test_replay_since_should_seed_current_file_at_eof_when_since_names_a_future_month(tmp_path):
    (tmp_path / "events-2026-09.jsonl").write_text(
        '{"event": "old", "ts": "2026-09-01T00:00:00Z"}\n'
    )
    seed: dict[Path, int] = {}

    list(
        event_log.replay_since(
            tmp_path, "2026-12", seed_out=seed, clock=FakeClock(start="2026-09-16T00:00:00Z")
        )
    )

    sep = tmp_path / "events-2026-09.jsonl"
    assert seed[sep] == sep.stat().st_size


def test_since_seed_should_leave_prior_month_at_eof_when_since_is_in_current_month(tmp_path):
    aug = tmp_path / "events-2026-08.jsonl"
    sep = tmp_path / "events-2026-09.jsonl"
    aug.write_text('{"event": "round_start", "ts": "2026-08-01T00:00:00Z"}\n')
    sep.write_text('{"event": "round_start", "ts": "2026-09-20T00:00:00Z"}\n')
    seed = {p: p.stat().st_size for p in event_log.newest_month_files(tmp_path, 2)}

    replayed = [line for line, _ in event_log.replay_since(tmp_path, "2026-09", seed_out=seed)]

    assert all("2026-08" not in line for line in replayed)
    assert seed[aug] == aug.stat().st_size


def test_read_new_should_return_all_rows_when_first_read(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")

    events_read, offsets = read_new([old], {})

    assert [e["n"] for e in events_read] == [1]
    assert offsets[old] == old.stat().st_size  # offset recorded at EOF


def test_read_new_should_return_only_appended_rows_when_file_grows(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")
    _, offsets = read_new([old], {})

    with old.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "round_end", "n": 2}) + "\n")
    appended_events, offsets = read_new([old], offsets)

    assert [e["n"] for e in appended_events] == [2]
    assert offsets[old] == old.stat().st_size


def test_read_new_should_read_new_file_from_start_when_rotated_in(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")
    _, offsets = read_new([old], {})

    new = tmp_path / "events-2026-09.jsonl"
    new.write_text(json.dumps({"event": "round_start", "n": 3}) + "\n")
    rotated_events, offsets = read_new([old, new], offsets)

    assert [e["n"] for e in rotated_events] == [3]  # new file read from byte 0
    assert offsets[old] == old.stat().st_size  # old untouched: no new bytes


def test_read_new_should_reread_from_start_when_truncated_below_offset(tmp_path: Path) -> None:
    # A 2-line file read once records its offset at the larger size; the state
    # dependency is load-bearing -- the truncation is only "below offset"
    # because the recorded offset is for the taller file.
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(
        json.dumps({"event": "round_start", "n": 1})
        + "\n"
        + json.dumps({"event": "round_end", "n": 2})
        + "\n"
    )
    _, offsets = read_new([old], {})

    old.write_text(json.dumps({"event": "round_start", "n": 4}) + "\n")
    reset_events, offsets = read_new([old], offsets)

    assert [e["n"] for e in reset_events] == [4]  # re-read from 0, not skipped
    assert offsets[old] == old.stat().st_size
