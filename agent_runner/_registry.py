"""Shared registry helpers for plugin-extension surfaces.

The `name`-keyed unique-registration check is identical across hooks
(:mod:`agent_runner.hooks`) and detectors (:mod:`agent_runner.monitor`).
This module is its single source of truth.

Event-kind registration in :mod:`agent_runner.events` has DIFFERENT
semantics (idempotent for same-source re-registration, conflict on
different-source) and stays in that module.
"""

from __future__ import annotations


def ensure_unique(name: str, existing: list, kind: str) -> None:
    """Raise ValueError if any item in ``existing`` already has ``.name == name``.

    ``kind`` is a short label embedded in the error message (e.g. ``"detector"``,
    ``"context_enricher"``).
    """
    for item in existing:
        if getattr(item, "name", None) == name:
            raise ValueError(f"{kind} {name!r} already registered; refusing to add a second")
