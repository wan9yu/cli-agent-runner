"""Structured event emitter — JSON Lines, monthly UTC naming.

Event kinds live in a two-tier registry:
- ``_BUILTIN_KINDS`` — frozen set of names emitted by core supervisor code.
- ``_PLUGIN_KINDS`` — mutable dict (name -> source label) populated by plugins
  via ``register_event_kind``. Loaded once at package import from setuptools
  ``entry_points`` group ``agent_runner.event_kinds``.

Public API:
- ``KNOWN_EVENT_KINDS`` — read-only union view; supports ``in`` and iteration.
  Preserved so ``from agent_runner.events import KNOWN_EVENT_KINDS`` still works.
- ``register_event_kind(name, *, source)`` — plugin entry point.
- ``plugin_event_kinds()`` — sorted list of currently-registered plugin names.
- ``emit(log_dir, kind, /, **fields)`` — append a structured event line.
  ``log_dir`` and ``kind`` are positional-only so callers can pass
  ``log_dir=...`` as a payload field name without parameter shadowing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Cross-module event-kind constants. Every module-level UPPER_CASE constant
# whose value is a snake_case string is automatically collected into
# _BUILTIN_KINDS via _collect_builtin_kinds() below (single-source).
AGENT_EXIT = "agent_exit"
ANOMALY_REPETITIVE_TOOL = "anomaly_repetitive_tool"
AGENT_NETWORK_BLIP = "agent_network_blip"
AGENT_SPAWN = "agent_spawn"
AGENT_USAGE_RECORDED = "agent_usage_recorded"
CONFIG_BROKEN = "config_broken"
CRASH_LOOP = "crash_loop"
DIRTY_AUTO_COMMITTED = "dirty_auto_committed"
DIRTY_COMMIT_FAILED = "dirty_commit_failed"
DIRTY_DETECTED = "dirty_detected"
FRESH_EYES_ROUND_TRIGGERED = "fresh_eyes_round_triggered"
HOOK_FAILED = "hook_failed"
MAX_ROUNDS_REACHED = "max_rounds_reached"
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
PROMPT_OVERWRITTEN = "prompt_overwritten"
ROUND_END = "round_end"
ROUND_GRACE_EXTENDED = "round_grace_extended"
ROUND_GRACE_KILL = "round_grace_kill"
ROUND_PROGRESS = "round_progress"
ROUND_START = "round_start"
ROUND_SUBSTRATE_AFTER = "round_substrate_after"
ROUND_SUBSTRATE_BEFORE = "round_substrate_before"
ROUND_TIMEOUT_KILL = "round_timeout_kill"
SELF_TERMINATED = "agent_self_terminated"
SERVE_STARTUP_HOOK_FAILED = "serve_startup_hook_failed"
SERVICE_UPGRADE_ROLLBACK_FAILED = "service_upgrade_rollback_failed"
SERVICE_UPGRADE_ROLLED_BACK = "service_upgrade_rolled_back"
SERVICE_UPGRADED = "service_upgraded"
SMOKE_CHECK_FAILED = "smoke_check_failed"
STATUS_RECOVERED = "status_recovered"
STOP_FILE_DETECTED = "stop_file_detected"
TRANSIENT_ERROR_BACKOFF_CAPPED = "transient_error_backoff_capped"
TRANSIENT_ERROR_DETECTED = "transient_error_detected"
TRANSIENT_ERROR_RECOVERED = "transient_error_recovered"


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
        if k.isupper()
        and isinstance(v, str)
        and v.islower()
        and not v.startswith("_")
        and v.replace("_", "").isalnum()
    )


_BUILTIN_KINDS: frozenset[str] = _collect_builtin_kinds()

_PLUGIN_KINDS: dict[str, str] = {}


def register_event_kind(name: str, *, source: str) -> None:
    """Register a plugin-supplied event kind.

    Raises ``ValueError`` if ``name`` collides with a built-in or with a
    different plugin source. Idempotent when the same source re-registers
    the same name (safe under repeated entry_points loading).
    """
    if name in _BUILTIN_KINDS:
        raise ValueError(f"event kind {name!r} is built-in; cannot re-register")
    existing = _PLUGIN_KINDS.get(name)
    if existing is not None and existing != source:
        raise ValueError(
            f"event kind {name!r} already registered by {existing!r}; "
            f"cannot re-register from {source!r}"
        )
    _PLUGIN_KINDS[name] = source


def _is_known(name: str) -> bool:
    return name in _BUILTIN_KINDS or name in _PLUGIN_KINDS


def plugin_event_kinds() -> list[str]:
    """Sorted list of currently-registered plugin event kind names."""
    return sorted(_PLUGIN_KINDS)


class _KnownEventKindsView:
    """Read-only union view of built-in + plugin event kinds.

    Backward compat for ``from agent_runner.events import KNOWN_EVENT_KINDS``.
    Supports ``in`` and ``iter``; intentionally does NOT support mutation.
    """

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and _is_known(item)

    def __iter__(self) -> Iterator[str]:
        yield from sorted(_BUILTIN_KINDS)
        yield from _PLUGIN_KINDS

    def __len__(self) -> int:
        return len(_BUILTIN_KINDS) + len(_PLUGIN_KINDS)

    def __repr__(self) -> str:
        return f"<KNOWN_EVENT_KINDS: {len(_BUILTIN_KINDS)} built-in + {len(_PLUGIN_KINDS)} plugin>"


KNOWN_EVENT_KINDS = _KnownEventKindsView()


def now_iso_ms() -> str:
    """UTC ISO-8601 timestamp with millisecond precision and trailing 'Z'.

    Shared helper — also used by metrics.py and runner.py for matching format.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{month}.jsonl"
    payload = {"ts": ts, "event": kind, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
