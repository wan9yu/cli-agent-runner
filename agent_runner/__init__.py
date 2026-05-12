"""Agent Runner — restart-on-exit supervisor for autonomous CLI agents."""

from __future__ import annotations

__version__ = "0.0.1"

_HOOK_GROUPS = (
    "agent_runner.pre_round_hooks",
    "agent_runner.context_enrichers",
    "agent_runner.post_round_hooks",
)


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


def _load_hook_plugins() -> None:
    """Discover and load entry_points plugins that register pre/post round
    hooks and context enrichers.

    Same failure-isolation contract as :func:`_load_event_kind_plugins`.
    """
    import warnings
    from importlib.metadata import entry_points

    for group in _HOOK_GROUPS:
        for ep in entry_points(group=group):
            try:
                ep.load()
            except Exception as e:
                warnings.warn(
                    f"failed to load {group} plugin {ep.name!r}: {e}",
                    stacklevel=2,
                )


def _load_detector_plugins() -> None:
    """Discover and load entry_points plugins that register custom monitor
    detectors via :func:`agent_runner.monitor.register_detector`.

    Same failure-isolation contract as the other loaders.
    """
    import warnings
    from importlib.metadata import entry_points

    for ep in entry_points(group="agent_runner.detectors"):
        try:
            ep.load()
        except Exception as e:
            warnings.warn(
                f"failed to load agent_runner.detectors plugin {ep.name!r}: {e}",
                stacklevel=2,
            )


_load_event_kind_plugins()
_load_hook_plugins()
_load_detector_plugins()
