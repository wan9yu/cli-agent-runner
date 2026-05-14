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

# Cross-module event-kind constants. Most kinds are emitted in only one place
# (runner.py), but kinds that are also CONSUMED elsewhere (filtered, surfaced
# in peek, asserted in tests) earn a constant to keep the spelling honest.
HOOK_FAILED = "hook_failed"
AGENT_NETWORK_BLIP = "agent_network_blip"
MONITOR_REMOTE_BLIP = "monitor_remote_blip"
MONITOR_REMOTE_GIVEUP = "monitor_remote_giveup"
PROMPT_OVERWRITTEN = "prompt_overwritten"
SERVICE_UPGRADED = "service_upgraded"
SERVICE_UPGRADE_ROLLED_BACK = "service_upgrade_rolled_back"
SERVICE_UPGRADE_ROLLBACK_FAILED = "service_upgrade_rollback_failed"

_BUILTIN_KINDS: frozenset[str] = frozenset(
    {
        "round_start",
        "agent_spawn",
        "agent_exit",
        AGENT_NETWORK_BLIP,
        "dirty_detected",
        "orphan_stashed",
        "orphan_idempotent_skip",
        "orphan_stash_failed",
        PROMPT_OVERWRITTEN,
        "round_timeout_kill",
        "sigterm_received",
        "status_recovered",
        "smoke_check_failed",
        "round_end",
        "monitor_alert_emitted",
        "monitor_auto_stop_failed",
        "monitor_auto_stop_triggered",
        MONITOR_REMOTE_BLIP,
        MONITOR_REMOTE_GIVEUP,
        "monitor_started",
        SERVICE_UPGRADED,
        SERVICE_UPGRADE_ROLLED_BACK,
        SERVICE_UPGRADE_ROLLBACK_FAILED,
        HOOK_FAILED,
    }
)

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
