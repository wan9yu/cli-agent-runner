"""Public API surface contract — what downstream plugin authors may import.

Rationale: plugin authors (e.g. Argus Gateway, future third-party packages)
register hooks/detectors/event-kinds against agent_runner.* public symbols.
This test snapshots the importable names so future refactors can't silently
remove them — a removal becomes a deliberate, reviewable change to this file.

Scope: this test guards the *public* API surface used by plugin code. It does
NOT cover private modules (anything starting with `_`), CLI internals, or the
agent_runtime.py subprocess module (intentionally not a plugin extension point).

Adding a name here: deliberate; mention in CHANGELOG.
Removing a name here: requires a major version bump (or 0.1.x breaking note
when pre-1.0).
"""

from __future__ import annotations

import importlib


def _public_names(module_path: str) -> set[str]:
    mod = importlib.import_module(module_path)
    names = getattr(mod, "__all__", None)
    if names is not None:
        return set(names)
    return {n for n in dir(mod) if not n.startswith("_")}


# Plugin-author public surface — alphabetised for diff readability.
EXPECTED_API_TYPES = {
    "Alert",
    "AutoAction",
    "Detector",
    "InitResult",
    "InstallResult",
    "ProjectState",
    "RoundResult",
    "RoundView",
    "ServiceMode",
    "ServiceStatus",
    "Severity",
    "SystemMetrics",
    "select_path",
}

EXPECTED_EVENTS_API = {
    "emit",
    "now_iso_ms",
    "parse_iso_ms",
    "register_event_kind",
    "plugin_event_kinds",
    "KNOWN_EVENT_KINDS",
}

EXPECTED_HOOKS_API = {
    "HookContext",
    "ContextEnricher",
    "PreRoundHook",
    "PostRoundHook",
    "register_context_enricher",
    "register_post_round_hook",
    "register_pre_round_hook",
    "context_enrichers",
    "post_round_hooks",
    "pre_round_hooks",
    "plugin_context_enrichers",
}

EXPECTED_MONITOR_API = {
    "AUTO_STOP_ALERTS",
    "KNOWN_ALERT_KINDS",
    "register_detector",
    "plugin_detectors",
}

EXPECTED_DETECTOR_HELPERS_API = {
    "cumulative_window_check",
    "dual_source_silence",
    "phase_filter",
}

EXPECTED_VCS_STATE_API = {
    "register_plugin_owned_paths",
    "plugin_owned_paths",
}

# Doomed symbols (removed in 0.1.7) — verify ABSENCE so a future revert can't
# silently restore them and re-couple core to Claude.
FORBIDDEN_AGENT_RUNTIME = {
    "CRITICAL_ENV_DEFAULTS",
    "merge_critical_envs",
}


def test_given_api_types_module_when_imported_then_public_surface_matches() -> None:
    actual = _public_names("agent_runner.api_types")
    missing = EXPECTED_API_TYPES - actual
    assert not missing, f"agent_runner.api_types: missing public names {missing}"


def test_given_events_module_when_imported_then_public_surface_matches() -> None:
    actual = _public_names("agent_runner.events")
    missing = EXPECTED_EVENTS_API - actual
    assert not missing, f"agent_runner.events: missing public names {missing}"


def test_given_hooks_module_when_imported_then_public_surface_matches() -> None:
    actual = _public_names("agent_runner.hooks")
    missing = EXPECTED_HOOKS_API - actual
    assert not missing, f"agent_runner.hooks: missing public names {missing}"


def test_given_monitor_module_when_imported_then_plugin_surface_matches() -> None:
    actual = _public_names("agent_runner.monitor")
    missing = EXPECTED_MONITOR_API - actual
    assert not missing, f"agent_runner.monitor: missing public names {missing}"


def test_given_detector_helpers_module_when_imported_then_public_surface_matches() -> None:
    actual = _public_names("agent_runner.detector_helpers")
    missing = EXPECTED_DETECTOR_HELPERS_API - actual
    assert not missing, f"agent_runner.detector_helpers: missing public names {missing}"


def test_given_agent_runtime_when_imported_then_claude_specific_symbols_absent() -> None:
    """0.1.7: CRITICAL_ENV_DEFAULTS + merge_critical_envs were removed. Their
    presence would indicate accidental restoration of Claude-specific coupling."""
    import agent_runner.agent_runtime as art

    present = FORBIDDEN_AGENT_RUNTIME & set(dir(art))
    assert not present, (
        f"agent_runner.agent_runtime: forbidden Claude-specific symbols present: {present}. "
        f"These were intentionally removed in 0.1.7 — env injection lives in AgentConfig.env."
    )


def test_given_vcs_state_module_when_imported_then_plugin_owned_paths_api_present() -> None:
    """0.1.8: register_plugin_owned_paths + plugin_owned_paths are the new
    plugin-author public surface. Lock them in so a future refactor can't
    silently rename or remove them."""
    actual = _public_names("agent_runner.vcs_state")
    missing = EXPECTED_VCS_STATE_API - actual
    assert not missing, (
        f"agent_runner.vcs_state: missing public names {missing}. "
        f"Plugin authors registered against the 0.1.8 names — do not remove without major bump."
    )
