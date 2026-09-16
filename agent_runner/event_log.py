"""Owns the month-sharded events-*.jsonl READ layout + offset/tail machinery.

One-way: imports events.py's parse primitives; events.py never imports this
(the write-side filename in events.emit stays local to avoid a cycle).

Three intent-level entry points sit on top of the shared layout selectors and
the offset-carrying read core:
- :func:`scan` -- stateless whole-scope forward re-scan (the detector family
  in ``_throttle.py``, the HTTP progress page's recent-events reader).
- :func:`follow` -- the shared tail loop (the CLI ``events --tail`` streamer
  and the monitor/observe poller both build on this).
- :func:`replay_since` -- the CLI streamer's ``--since`` backlog replay.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runner.clock import SYSTEM_CLOCK, Clock
from agent_runner.events import _iter_parsed_lines, iter_event_dicts, open_events_jsonl

Scope = Callable[[Path], list[Path]]
"""``log_dir -> in-scope files, OLDEST -> NEWEST``."""

_GLOB = "events-*.jsonl"


def month_of(path: Path) -> str:
    """The ``YYYY-MM`` embedded in an ``events-YYYY-MM.jsonl`` filename."""
    return path.stem[len("events-") :]


def month_of_datetime(dt: datetime) -> str:
    """The ``YYYY-MM`` month key a timestamp maps to (mirror of :func:`month_of`
    for the ``--since`` filter, which compares against this module's shards)."""
    return dt.astimezone(UTC).strftime("%Y-%m")


def events_path(log_dir: Path, month: str) -> Path:
    return log_dir / f"events-{month}.jsonl"


def current_month_file(log_dir: Path, *, clock: Clock = SYSTEM_CLOCK) -> Path:
    return events_path(log_dir, clock.now_utc().strftime("%Y-%m"))


def all_month_files(log_dir: Path) -> list[Path]:
    """Every events-*.jsonl file, oldest -> newest. A :data:`Scope`."""
    return sorted(log_dir.glob(_GLOB))


def newest_month_files(log_dir: Path, n: int = 2) -> list[Path]:
    """The newest ``n`` events-*.jsonl files, oldest -> newest (an ascending
    suffix of :func:`all_month_files`)."""
    return all_month_files(log_dir)[-n:]


def newest_scope(n: int = 2) -> Scope:
    return lambda log_dir: newest_month_files(log_dir, n)


def read_new_raw(
    paths: Iterable[Path], offsets: dict[Path, int]
) -> tuple[list[tuple[str, dict]], dict[Path, int]]:
    """Read events appended to ``paths`` since ``offsets``, preserving each
    line's ORIGINAL text alongside its parsed dict -- the BASE read;
    :func:`read_new` is a dict-only projection of this.

    Same contract as the relocated ``events.read_new`` (verbatim): a missing
    path (``FileNotFoundError`` on stat) yields nothing and keeps its prior
    offset; a size smaller than the recorded offset means truncated/replaced
    beneath us, so it re-reads from byte 0; an unseen path defaults to offset
    0, so a newly rotated-in file reads as new from its start.
    """
    out: list[tuple[str, dict]] = []
    new_offsets = dict(offsets)
    for path in paths:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        pos = new_offsets.get(path, 0)
        if size < pos:
            pos = 0
        if size == pos:
            continue  # match events.read_new exactly (bare continue, no offset write)
        with open_events_jsonl(path) as f:
            f.seek(pos)
            for line, obj in _iter_parsed_lines(f):
                out.append((line, obj))
            new_offsets[path] = f.tell()
    return out, new_offsets


def read_new(
    paths: Iterable[Path], offsets: dict[Path, int]
) -> tuple[list[dict[str, Any]], dict[Path, int]]:
    """Dict-only projection of :func:`read_new_raw` -- the single tailer every
    offset-carrying events-*.jsonl reader that doesn't need raw bytes composes
    (monitor poll, the rolling event buffer)."""
    records, new_offsets = read_new_raw(paths, offsets)
    return [obj for _line, obj in records], new_offsets


def seed_at_eof(files: Iterable[Path]) -> dict[Path, int]:
    """Per-path offsets at each file's current EOF -- the "start after the
    existing backlog" seed for :func:`follow`. Skips any file that vanishes
    between glob and stat (TOCTOU), matching the pre-consolidation tolerance."""
    out: dict[Path, int] = {}
    for path in files:
        try:
            out[path] = path.stat().st_size
        except OSError:
            continue
    return out


def scan(log_dir: Path, scope: Scope, *, tolerant: bool = False) -> Iterator[dict]:
    """STATELESS whole-scope forward (old->new) re-scan -- no offset carried
    between calls. A ``detected`` event can be thousands of lines back, so the
    detector family in ``_throttle.py`` re-scans its whole newest-2 window
    every call rather than risk an offset cursor silently skipping (and
    forgetting) a live throttle.

    ``tolerant``: skip a file that vanishes/errors between glob and read rather
    than raising -- for the http progress page, where a pruned file should
    degrade the view, not 500 it."""
    for path in scope(log_dir):
        try:
            yield from iter_event_dicts(path)
        except OSError:
            if not tolerant:
                raise


def follow(
    log_dir: Path,
    scope: Scope,
    *,
    wake: Any,
    timeout_s: float,
    offsets: dict[Path, int] | None = None,
) -> Iterator[tuple[str, dict]]:
    """The shared tail loop: re-scan ``scope(log_dir)`` for new bytes since
    ``offsets``, yielding each new ``(line, dict)`` pair; when nothing new is
    found, block on ``wake.wait(timeout_s)`` before trying again.

    ``wake`` is a ``_notify.Listener`` (FIFO doorbell -- wakes within
    milliseconds of a ``events.emit`` write) or ``_notify.NULL_LISTENER``
    (degrades to a plain ``clock.sleep(timeout_s)``); both share the same
    ``.wait(timeout_s)`` signature, so this is the one pluggable seam between
    a doorbell-driven caller and a plain poller -- no separate flag needed.
    Never returns on its own; the caller stops it (SIGINT, a bounded loop,
    or simply abandoning the generator).
    """
    offsets = dict(offsets) if offsets else {}
    while True:
        records, offsets = read_new_raw(scope(log_dir), offsets)
        if records:
            yield from records
        else:
            wake.wait(timeout_s)


def replay_since(
    log_dir: Path,
    since_month: str,
    *,
    seed_out: dict[Path, int],
    clock: Clock = SYSTEM_CLOCK,
) -> Iterator[tuple[str, dict]]:
    """Replay every ``(line, dict)`` pair from every month file at or after
    ``since_month``, oldest month first; older months are never opened.

    ``seed_out`` is mutated in place with the exact-byte handoff for the
    CURRENT month file: the true end-of-read position once replay finishes
    (or, if ``since_month`` names a FUTURE month so the current file is
    skipped as "older", its end-of-file size) -- the seed a caller's
    subsequent :func:`follow` call resumes from, so a line appended mid-replay
    is picked up by the follow phase instead of being skipped, and no line is
    emitted in both phases.
    """
    current = current_month_file(log_dir, clock=clock)
    for path in all_month_files(log_dir):
        if month_of(path) < since_month:
            # Nothing to replay from a month that ended before `since`. The
            # live file lands here only when `since` names a future month --
            # seed the tail at its end rather than rewinding it to 0.
            if path == current:
                seed_out[path] = path.stat().st_size
            continue
        with open_events_jsonl(path) as f:
            yield from _iter_parsed_lines(f)
            if path == current:
                seed_out[path] = f.tell()
