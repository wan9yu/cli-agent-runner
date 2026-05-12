"""Agent Runner — restart-on-exit supervisor for autonomous CLI agents."""

from __future__ import annotations

__version__ = "0.0.1"

_HOOK_GROUPS = (
    "agent_runner.pre_round_hooks",
    "agent_runner.context_enrichers",
    "agent_runner.post_round_hooks",
)


def _load_plugins_from_group(group: str) -> None:
    """Discover and load entry_points in ``group``, isolating per-plugin failures.

    Called at package import. A broken plugin must not crash the supervisor;
    each failure surfaces as a ``UserWarning``.
    """
    import warnings
    from importlib.metadata import entry_points

    for ep in entry_points(group=group):
        try:
            ep.load()
        except Exception as e:
            warnings.warn(
                f"failed to load {group} plugin {ep.name!r}: {e}",
                stacklevel=3,
            )


def _load_event_kind_plugins() -> None:
    """Load plugins that register custom event kinds via ``events.register_event_kind``."""
    _load_plugins_from_group("agent_runner.event_kinds")


def _load_hook_plugins() -> None:
    """Load plugins that register pre_round / context_enricher / post_round hooks."""
    for group in _HOOK_GROUPS:
        _load_plugins_from_group(group)


def _load_detector_plugins() -> None:
    """Load plugins that register custom monitor detectors via ``monitor.register_detector``."""
    _load_plugins_from_group("agent_runner.detectors")


_load_event_kind_plugins()
_load_hook_plugins()
_load_detector_plugins()
