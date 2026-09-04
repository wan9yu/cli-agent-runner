"""Public API surface contract — what downstream plugin authors may import.

Rationale: plugin authors (e.g. a downstream integrator, future third-party packages)
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
    "DirtyOutcome",
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
    "DirtyHandler",
    "PreRoundHook",
    "PostRoundHook",
    "ServeStartupHook",
    "register_context_enricher",
    "register_dirty_handler",
    "register_post_round_hook",
    "register_pre_round_hook",
    "register_serve_startup_hook",
    "context_enrichers",
    "post_round_hooks",
    "pre_round_hooks",
    "serve_startup_hooks",
    "plugin_context_enrichers",
}

EXPECTED_MONITOR_API = {
    "AUTO_STOP_ALERTS",
    "KNOWN_ALERT_KINDS",
    "register_detector",
    "plugin_detectors",
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


def test_given_agent_runtime_when_imported_then_claude_specific_symbols_absent() -> None:
    """0.1.7: CRITICAL_ENV_DEFAULTS + merge_critical_envs were removed. Their
    presence would indicate accidental restoration of Claude-specific coupling."""
    import agent_runner.agent_runtime as art

    present = FORBIDDEN_AGENT_RUNTIME & set(dir(art))
    assert not present, (
        f"agent_runner.agent_runtime: forbidden Claude-specific symbols present: {present}. "
        f"These were intentionally removed in 0.1.7 — env injection lives in AgentConfig.env."
    )


def test_given_cancel_removed_when_public_surface_inspected_then_absent() -> None:
    """0.2.2 deletes `cancel`: CLI verb, api.cancel(), and the SIGUSR1 machinery.

    It never delivered the interrupt semantics it documented -- nothing ever
    wrote round.pid, so the SIGINT forward was unreachable. `stop` replaces it.
    """
    import inspect

    from agent_runner import api
    from agent_runner.cli import _build_parser, serve_cmd

    assert not hasattr(api, "cancel")
    sub = next(a for a in _build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "cancel" not in sub.choices
    src = inspect.getsource(serve_cmd)
    assert "round.pid" not in src
    assert "SIGUSR1" not in src


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


# Baseline pin for the 0.2.12 Group G split (api.py -> _serve_policy et al.).
# This is `_public_names("agent_runner.api")` frozen at HEAD before the split
# starts: EVERY name `dir(agent_runner.api)` exposes today, minus underscored
# ones -- including stdlib/typing re-exports (Path, Any, Literal, ...),
# submodules imported at module scope (events, monitor, lifecycle, ...), and
# genuine public API (start/stop/..., emit_* wrappers, read_round_num, ...).
# Exact equality (not subset) is intentional: G1-G3 must not drop OR add a
# name here silently. If a later task deliberately changes api's surface,
# update this set in the same commit and say why.
#
# G1 (extract restart policy into _serve_policy.py) drops "Literal": it was
# only ever a side effect of `post_round_decision`'s return-type annotation
# living in api.py, not a name plugin authors could meaningfully use. With
# that function (and its only `Literal[...]` usage) moved to _serve_policy,
# api.py no longer imports `Literal` at module scope, so it no longer appears
# in `dir(api)`. No genuine public API name was removed.
EXPECTED_API_SURFACE = {
    "AGENT_NETWORK_BLIP",
    "Any",
    "AutoCommitError",
    "CRASH_LOOP_EXIT",
    "CRASH_LOOP_MAX_DELAY_S",
    "CRASH_LOOP_SHORT_EXIT_S",
    "CRASH_LOOP_THRESHOLD",
    "Config",
    "ENV_BATTERY_EXIT",
    "HOOK_FAILED",
    "InitResult",
    "InstallResult",
    "Iterator",
    "MEM_LOOP_EXIT",
    "MONITOR_STARTED",
    "PERMANENT_CONFIG_EXIT",
    "PIDFile",
    "Path",
    "ProjectState",
    "RateLimitState",
    "RuntimeConfig",
    "SYSTEM_CLOCK",
    "Sequence",
    "ServiceMode",
    "ServiceStatus",
    "StashError",
    "TextIO",
    "annotations",
    "assemble_prompt",
    "check_self_terminated_sentinel",
    "dataclasses",
    "defenses",
    "detect_service_mode",
    "emit_agent_auth_error_detected",
    "emit_agent_usage_recorded",
    "emit_anomaly_repetitive_tool",
    "emit_config_broken",
    "emit_config_migrated",
    "emit_crash_loop",
    "emit_fresh_eyes_round_triggered",
    "emit_max_rounds_reached",
    "emit_mem_loop",
    "emit_rate_limit_stop",
    "emit_round_deferred",
    "emit_round_grace_extended",
    "emit_round_grace_kill",
    "emit_round_logs_prune_deferred",
    "emit_round_mem_terminated",
    "emit_round_progress",
    "emit_round_resumed",
    "emit_round_substrate_after",
    "emit_round_substrate_before",
    "emit_round_supervisor_wedged",
    "emit_schedule_paused",
    "emit_schedule_phase_skipped",
    "emit_schedule_resumed",
    "emit_stale_index_lock_cleared",
    "emit_stop_file_detected",
    "emit_transient_error_backoff_capped",
    "emit_transient_error_detected",
    "emit_transient_error_recovered",
    "events",
    "init",
    "install",
    "kill",
    "lifecycle",
    "load_config",
    "monitor",
    "monitor_loop",
    "monitor_unit_filename",
    "narrate_events",
    "os",
    "outer_round_ceiling_s",
    "peek",
    "pid_alive",
    "post_round_decision",
    # "re" dropped 0.2.13 (Group C): project-name regex validation moved to
    # _resolve.py, api.py no longer needs `import re` for its own sake.
    "read_round_num",
    "read_sentinel_content",
    "relay_remote_events",
    "render_monitor_unit",
    "render_serve_unit",
    "resolve_runtime_for_phase",
    "restart",
    "scaffold_project",
    "select_path",
    "send_signal_to_pid",
    "serve_unit_filename",
    "shutil",
    "signal",
    "start",
    "stash_orphan",
    "status",
    "stop",
    "stream_events_jsonl",
    "subprocess",
    "sysconfig",
    "try_auto_commit",
    "uninstall",
    # "wait_until" added 0.2.13 (simplify pass): api._await_pid_exit now shares
    # the poll-and-confirm loop with lifecycle.stop_unit_draining via
    # clock.wait_until, imported at module scope alongside SYSTEM_CLOCK.
    "wait_until",
}


def test_given_api_module_when_imported_then_public_surface_pinned() -> None:
    """0.2.12 Group G0: freeze api's importable surface before the split.

    Uses exact equality (not the subset check other tests in this file use)
    so G1-G3 fail loudly on either a dropped name (a real regression) or an
    added one (a deliberate change that must update this pin in the same
    commit)."""
    actual = _public_names("agent_runner.api")
    missing = EXPECTED_API_SURFACE - actual
    added = actual - EXPECTED_API_SURFACE
    assert not missing and not added, (
        f"agent_runner.api public surface drifted from the 0.2.12 Group G0 "
        f"baseline pin -- missing: {missing or '{}'}, added: {added or '{}'}. "
        f"If this split intentionally changed api's public surface, update "
        f"EXPECTED_API_SURFACE in this file in the same commit and explain why."
    )
