"""Shared registry helpers for plugin-extension surfaces.

The `name`-keyed unique-registration check is identical across hooks
(:mod:`agent_runner.hooks`) and detectors (:mod:`agent_runner.monitor`).
This module is its single source of truth.

Event-kind registration in :mod:`agent_runner.events` has DIFFERENT
semantics (idempotent for same-source re-registration, conflict on
different-source) and stays in that module.
"""

from __future__ import annotations

from typing import Any

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
exactly (pinned by test_builtin_plugin_names_sync). A reserved name is NECESSARY
but not SUFFICIENT for builtin trust — see ``is_builtin_provenance``: the name
alone is collidable, so trust also requires genuine provenance."""

_BUILTIN_MODULE_PREFIX = "agent_runner.builtin_plugins."


def is_builtin_provenance(name: str, module_path: str) -> bool:
    """A plugin is a trusted builtin ONLY if its name is reserved AND its
    module is genuinely part of this package. Third-party code cannot place a
    module under agent_runner.builtin_plugins, so a name match without this
    prefix is a name-squatter, never a builtin."""
    return name in BUILTIN_PLUGIN_NAMES and module_path.startswith(_BUILTIN_MODULE_PREFIX)


def resolve_entry_target(module_path: str, attr_path: str) -> Any:
    """Import ``module_path`` and walk ``attr_path`` (dot-separated, empty
    segments ignored) via ``getattr``, returning the final target — the
    module itself when ``attr_path`` is empty.

    The single resolution recipe for an entry-point's ``module:attr`` value.
    Shared by the plugin loader (``agent_runner.load_and_register_plugins``)
    and the sandbox trampoline children (``_plugin_sandbox._run_dirty_child``
    / ``_run_spawn_child``) — dependency-free (stdlib ``importlib`` only) so
    it's safe to import before a child self-restricts.
    """
    import importlib

    target: Any = importlib.import_module(module_path)
    for attr in filter(None, attr_path.split(".")):
        target = getattr(target, attr)
    return target


def ensure_unique(name: str, existing: list, kind: str) -> None:
    """Raise ValueError if any item in ``existing`` already has ``.name == name``.

    ``kind`` is a short label embedded in the error message (e.g. ``"detector"``,
    ``"context_enricher"``).
    """
    for item in existing:
        if getattr(item, "name", None) == name:
            raise ValueError(f"{kind} {name!r} already registered; refusing to add a second")
