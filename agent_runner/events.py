"""Structured event emitter — JSON Lines, monthly UTC naming.

``_BUILTIN_KINDS`` is a frozen set of every event kind emitted by core
supervisor code, collected by reflection (see ``_collect_builtin_kinds``).

Public API:
- ``KNOWN_EVENT_KINDS`` — the set of known kinds (builtin + plugin-registered);
  supports ``in`` and iteration. Preserved so
  ``from agent_runner.events import KNOWN_EVENT_KINDS`` still works.
- ``register_plugin_kind(name)`` — an out-of-tree plugin registers a
  namespaced custom event kind so ``emit()`` accepts it.
- ``emit(log_dir, kind, /, **fields)`` — append a structured event line.
  ``log_dir`` and ``kind`` are positional-only so callers can pass
  ``log_dir=...`` as a payload field name without parameter shadowing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from agent_runner import _notify
from agent_runner.clock import SYSTEM_CLOCK

# Cross-module event-kind constants. Every module-level UPPER_CASE constant
# whose value is a snake_case string is automatically collected into
# _BUILTIN_KINDS via _collect_builtin_kinds() below (single-source).
AGENT_AUTH_ERROR_DETECTED = "agent_auth_error_detected"
AGENT_EXIT = "agent_exit"
ANOMALY_REPETITIVE_TOOL = "anomaly_repetitive_tool"
AGENT_NETWORK_BLIP = "agent_network_blip"
AGENT_SPAWN = "agent_spawn"
AGENT_USAGE_RECORDED = "agent_usage_recorded"
CGROUP_GROWTH_RATE_WARNING = "cgroup_growth_rate_warning"
CONFIG_BROKEN = "config_broken"
CONFIG_MIGRATED = "config_migrated"
CRASH_LOOP = "crash_loop"
DETECTOR_ERROR = "detector_error"
DIRTY_AUTO_COMMITTED = "dirty_auto_committed"
DIRTY_CHECK_FAILED = "dirty_check_failed"
DIRTY_COMMIT_FAILED = "dirty_commit_failed"
DIRTY_DETECTED = "dirty_detected"
FRESH_EYES_ROUND_TRIGGERED = "fresh_eyes_round_triggered"
GOAL_ASSESSMENT = "goal_assessment"
GOAL_CHECK = "goal_check"
HOOK_FAILED = "hook_failed"
HOST_CGROUP_MEMORY_LIMIT = "host_cgroup_memory_limit"
MAX_ROUNDS_REACHED = "max_rounds_reached"
MEMORY_HIGH_ENGAGED = "memory_high_engaged"
MEMORY_HIGH_RELEASED = "memory_high_released"
MEMORY_HIGH_WRITE_FAILED = "memory_high_write_failed"
MEM_LOOP = "mem_loop"
MEM_LOOP_PERSISTENT = "mem_loop_persistent"
MEM_PRESSURE_DEFERRED_TO_CGROUP = "mem_pressure_deferred_to_cgroup"
MONITOR_ALERT_EMITTED = "monitor_alert_emitted"
MONITOR_AUTO_STOP_FAILED = "monitor_auto_stop_failed"
MONITOR_AUTO_STOP_TRIGGERED = "monitor_auto_stop_triggered"
MONITOR_REMOTE_BLIP = "monitor_remote_blip"
MONITOR_REMOTE_GIVEUP = "monitor_remote_giveup"
MONITOR_STARTED = "monitor_started"
ORPHAN_IDEMPOTENT_SKIP = "orphan_idempotent_skip"
ORPHAN_STASH_FAILED = "orphan_stash_failed"
ORPHAN_STASHED = "orphan_stashed"
PACKAGE_UPGRADED = "package_upgraded"
ROUND_CGROUP_MEMORY = "round_cgroup_memory"
ROUND_CONTAINER_ORPHAN_RISK = "round_container_orphan_risk"
ROUND_DEFERRED = "round_deferred"
ROUND_END = "round_end"
ROUND_GRACE_EXTENDED = "round_grace_extended"
ROUND_GRACE_KILL = "round_grace_kill"
ROUND_LOGS_PRUNE_DEFERRED = "round_logs_prune_deferred"
ROUND_MEM_CRITICAL_SAMPLE = "round_mem_critical_sample"
ROUND_MEM_TERMINATED = "round_mem_terminated"
ROUND_OOM_KILLED = "round_oom_killed"
ROUND_PROGRESS = "round_progress"
ROUND_RESUMED = "round_resumed"
ROUND_START = "round_start"
ROUND_SUBSTRATE_AFTER = "round_substrate_after"
ROUND_SUBSTRATE_BEFORE = "round_substrate_before"
ROUND_SUPERVISOR_WEDGED = "round_supervisor_wedged"
ROUND_TIMEOUT_KILL = "round_timeout_kill"
SCHEDULE_PAUSED = "schedule_paused"
SCHEDULE_PHASE_SKIPPED = "schedule_phase_skipped"
SCHEDULE_RESUMED = "schedule_resumed"
SELF_TERMINATED = "agent_self_terminated"
SERVICE_UPGRADE_ROLLBACK_FAILED = "service_upgrade_rollback_failed"
SERVICE_UPGRADE_ROLLED_BACK = "service_upgrade_rolled_back"
SERVICE_UPGRADED = "service_upgraded"
SESSION_RESUMED = "session_resumed"
SMOKE_CHECK_FAILED = "smoke_check_failed"
STALE_INDEX_LOCK_CLEARED = "stale_index_lock_cleared"
STALLED_NO_PROGRESS = "stalled_no_progress"
STATUS_RECOVERED = "status_recovered"
STOP_FILE_DETECTED = "stop_file_detected"
TRANSIENT_ERROR_BACKOFF_CAPPED = "transient_error_backoff_capped"
TRANSIENT_ERROR_DETECTED = "transient_error_detected"
TRANSIENT_ERROR_RECOVERED = "transient_error_recovered"
UPGRADE_START_FAILED = "upgrade_start_failed"


def _is_snake_case_kind(s: str) -> bool:
    """The legal event-kind name rule: lowercase snake_case, no leading '_'.

    Single source shared by _collect_builtin_kinds (filtering this module's
    constants) and register_plugin_kind (validating plugin-supplied names) so
    the two cannot drift if the naming rule ever changes.
    """
    return s.islower() and not s.startswith("_") and s.replace("_", "").isalnum()


def _collect_builtin_kinds() -> frozenset[str]:
    """Single-source: every module-level UPPER_CASE str constant whose value
    is a snake_case kind name is a builtin event kind. Drift between the
    constant list and a hand-maintained set is impossible by construction.
    """
    import sys

    mod = sys.modules[__name__]
    return frozenset(
        v
        for k, v in vars(mod).items()
        if k.isupper() and isinstance(v, str) and _is_snake_case_kind(v)
    )


_BUILTIN_KINDS: frozenset[str] = _collect_builtin_kinds()

# Runtime-registered plugin event kinds (out-of-tree producers). Unlike
# _BUILTIN_KINDS this is mutable and populated at import/registration time by
# plugin code via register_plugin_kind(), not by reflection over this module.
_PLUGIN_KINDS: set[str] = set()


def _is_known(name: str) -> bool:
    return name in _BUILTIN_KINDS or name in _PLUGIN_KINDS


def register_plugin_kind(name: str) -> None:
    """Register a namespaced custom event kind for an out-of-tree plugin.

    Restores a minimal affordance a prior release's subtraction of the
    ``event_kinds`` manifest field removed: a plugin installed on a target
    host still needs a validated way to emit its own event kinds through the
    same ``emit()``/events-*.jsonl stream core uses, rather than
    hand-appending JSON lines (unvalidated, and liable to collide with a
    builtin kind).

    ``name`` must be lowercase snake_case, contain at least one ``_`` (so it
    is namespaced — e.g. ``myplugin_ok``), and its first underscore-segment
    must not be a prefix any builtin kind uses (derived programmatically from
    _BUILTIN_KINDS, not hand-maintained — e.g. ``round_myplugin`` collides
    with the ``round_*`` builtin namespace even though ``round_myplugin``
    itself isn't a builtin kind). Raises ValueError on any violation.
    """
    if not isinstance(name, str) or not name or not _is_snake_case_kind(name):
        raise ValueError(f"invalid plugin event kind: {name!r}")
    if "_" not in name:
        raise ValueError(f"plugin event kind must be namespaced (contain '_'): {name!r}")
    # An exact builtin collision is also caught by the prefix check below (a
    # builtin's own first-segment is always a builtin prefix), but flag it here
    # for a clearer, more specific error message.
    if name in _BUILTIN_KINDS:
        raise ValueError(f"plugin event kind collides with a builtin kind: {name!r}")
    prefix = name.split("_", 1)[0]
    builtin_prefixes = {k.split("_", 1)[0] for k in _BUILTIN_KINDS}
    if prefix in builtin_prefixes:
        raise ValueError(f"plugin event kind {name!r} uses builtin-owned prefix {prefix!r}")
    _PLUGIN_KINDS.add(name)


class _KnownEventKindsView:
    """Read-only view of known event kinds — builtin plus plugin-registered.

    Backward compat for ``from agent_runner.events import KNOWN_EVENT_KINDS``.
    Supports ``in`` and ``iter``; intentionally does NOT support mutation
    (use ``register_plugin_kind()`` to add a plugin kind).
    """

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and _is_known(item)

    def __iter__(self) -> Iterator[str]:
        yield from sorted(_BUILTIN_KINDS | _PLUGIN_KINDS)

    def __len__(self) -> int:
        return len(_BUILTIN_KINDS | _PLUGIN_KINDS)

    def __repr__(self) -> str:
        total = len(_BUILTIN_KINDS | _PLUGIN_KINDS)
        return (
            f"<KNOWN_EVENT_KINDS: {total} known "
            f"({len(_BUILTIN_KINDS)} built-in, {len(_PLUGIN_KINDS)} plugin)>"
        )


KNOWN_EVENT_KINDS = _KnownEventKindsView()


def now_iso_ms() -> str:
    """UTC ISO-8601 timestamp with millisecond precision and trailing 'Z'.

    Shared helper — also used by metrics.py and runner.py for matching format.
    """
    return SYSTEM_CLOCK.now_utc().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso_ms(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp produced by :func:`now_iso_ms` (trailing ``Z``).

    Centralizes the ``replace("Z", "+00:00")`` workaround so the eventual cleanup
    (once ``datetime.fromisoformat`` accepts ``Z`` natively) is a single edit.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def emit(log_dir: Path, kind: str, /, **fields: Any) -> None:
    """Append one event line to events-YYYY-MM.jsonl (UTC).

    Caller must ensure ``log_dir`` exists (runner.run_one_round does this once
    per round; tests use the ``tmp_log_dir`` fixture which creates it).

    ``log_dir`` and ``kind`` are positional-only so callers can pass
    ``log_dir=`` as a payload field name without parameter shadowing.
    """
    if not _is_known(kind):
        raise ValueError(f"unknown event kind: {kind!r}")
    now = SYSTEM_CLOCK.now_utc()
    month = now.strftime("%Y-%m")
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{month}.jsonl"
    payload = {"ts": ts, "event": kind, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _notify.ring(log_dir)


def open_events_jsonl(path: Path) -> TextIO:
    """Text-mode opener for events-*.jsonl reads, pinning the read-side decode
    policy: utf-8 with a lossy fallback on bad bytes.

    Every ``events-*.jsonl`` reader (serve's throttle scan, monitor, peek, the
    ``events`` verb, the HTTP progress page) goes through here so one place
    owns the decode policy — mirrors ``round_log.open_round_log``'s role for
    round logs. A single corrupt byte (SD-card bit-rot on the Pi targets)
    degrades to a replacement char instead of raising ``UnicodeDecodeError``
    at every reader.
    """
    return path.open("r", encoding="utf-8", errors="replace")


def _iter_parsed_lines(f: TextIO) -> Iterator[tuple[str, dict]]:
    """Parse an already-open JSONL stream, skipping blank/malformed lines and
    any line that decodes to something other than a JSON object.

    Yields ``(stripped_line, parsed_dict)`` so a caller that also needs the
    original text (to print it verbatim) or the file's post-read position
    (offset-tailing) keeps both without re-parsing. The dict-only surface for
    the common whole-file case is :func:`iter_event_dicts`, below.
    """
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield line, obj


def iter_event_dicts(path: Path) -> Iterator[dict]:
    """Parse an entire events-*.jsonl file into dicts.

    The shared parse+dict-guard every whole-file events-*.jsonl reader needs:
    skip blank/malformed lines and any line that decodes to something other
    than a JSON object (a bare number/string/list line must not reach a
    caller's ``.get(...)``).
    """
    with open_events_jsonl(path) as f:
        for _, obj in _iter_parsed_lines(f):
            yield obj
