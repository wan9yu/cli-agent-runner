"""Shared registry helpers for plugin-extension surfaces.

The `name`-keyed unique-registration check is identical across hooks
(:mod:`agent_runner.hooks`) and detectors (:mod:`agent_runner.monitor`).
This module is its single source of truth.

Event-kind registration in :mod:`agent_runner.events` has DIFFERENT
semantics (idempotent for same-source re-registration, conflict on
different-source) and stays in that module.
"""

from __future__ import annotations

BUILTIN_PLUGIN_NAMES: frozenset[str] = frozenset(
    {
        "claude_rate_limit",
        "gemini",
        "codewhale",
        "kimi",
        "pi",
        "default_dirty_handler",
    }
)
"""Mirrors pyproject.toml's [project.entry-points."agent_runner.plugins"] table
exactly (pinned by test_builtin_plugin_names_sync). This frozenset is the ONE
place trust-by-identity is decided, so drift here is a silent trust boundary bug."""


def ensure_unique(name: str, existing: list, kind: str) -> None:
    """Raise ValueError if any item in ``existing`` already has ``.name == name``.

    ``kind`` is a short label embedded in the error message (e.g. ``"detector"``,
    ``"context_enricher"``).
    """
    for item in existing:
        if getattr(item, "name", None) == name:
            raise ValueError(f"{kind} {name!r} already registered; refusing to add a second")
