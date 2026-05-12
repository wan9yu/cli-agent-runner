"""Agent Runner — restart-on-exit supervisor for autonomous CLI agents."""

from __future__ import annotations

__version__ = "0.0.1"


def _load_event_kind_plugins() -> None:
    """Discover and load entry_points plugins that register custom event kinds.

    Called once at package import. Failures are non-fatal — a broken plugin
    must not crash the supervisor. Each failure surfaces as a ``UserWarning``
    so operators can spot misconfigured plugins without losing the service.
    """
    import warnings
    from importlib.metadata import entry_points

    for ep in entry_points(group="agent_runner.event_kinds"):
        try:
            ep.load()
        except Exception as e:
            warnings.warn(
                f"failed to load agent_runner.event_kinds plugin {ep.name!r}: {e}",
                stacklevel=2,
            )


_load_event_kind_plugins()
