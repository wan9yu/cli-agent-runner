"""Shared registry helpers for plugin-extension surfaces.

The `name`-keyed unique-registration check (:func:`ensure_unique`) is shared
across every hook family in :mod:`agent_runner.hooks` (post_round_hooks) and
by :mod:`agent_runner._plugin_manifest`'s own manifest-name check. This
module is its single source of truth.
"""

from __future__ import annotations

from typing import Any


def resolve_entry_target(module_path: str, attr_path: str) -> Any:
    """Import ``module_path`` and walk ``attr_path`` (dot-separated, empty
    segments ignored) via ``getattr``, returning the final target — the
    module itself when ``attr_path`` is empty.

    The single resolution recipe for an entry-point's ``module:attr`` value,
    used by the plugin loader (``agent_runner.load_and_register_plugins``).
    """
    import importlib

    target: Any = importlib.import_module(module_path)
    for attr in filter(None, attr_path.split(".")):
        target = getattr(target, attr)
    return target


def ensure_unique(name: str, existing: list, kind: str) -> None:
    """Raise ValueError if any item in ``existing`` already has ``.name == name``.

    ``kind`` is a short label embedded in the error message (e.g. ``"post_round_hook"``,
    ``"plugin manifest"``).
    """
    for item in existing:
        if getattr(item, "name", None) == name:
            raise ValueError(f"{kind} {name!r} already registered; refusing to add a second")
