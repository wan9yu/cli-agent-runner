"""Event emission wrappers — extracted from api.py for module-size hygiene.

Public facade over the ``_emit`` package: every sibling module in
``agent_runner`` (and every test) imports from ``agent_runner._emit``
directly, never from ``agent_runner._emit.<submodule>`` — this file is the
one place a name crosses that boundary. All wrappers are re-exported from
agent_runner.api for backward compatibility; plugins and supervisor should
continue importing from agent_runner.api (e.g.
``from agent_runner.api import emit_transient_error_detected``).

Each wrapper exists to keep cli/serve_cmd.py from importing agent_runner.events
directly — preserves the module-boundary architecture invariant. Local-import pattern
inside each wrapper body keeps agent_runner.api import-cheap.

Submodules are organised by domain: ``serve`` (supervisor control plane +
config/schedule lifecycle), ``rounds`` (one round's own lifecycle),
``memory`` (host/cgroup memory pressure), ``agent`` (the agent axis — plugin
reports + throttle response).
"""

from __future__ import annotations

from agent_runner._emit.agent import (  # noqa: F401 — public re-export
    emit_agent_auth_error_detected,
    emit_agent_usage_recorded,
    emit_anomaly_repetitive_tool,
    emit_transient_error_backoff_capped,
    emit_transient_error_detected,
    emit_transient_error_recovered,
)
from agent_runner._emit.memory import (  # noqa: F401 — public re-export
    emit_host_cgroup_memory_limit,
    emit_mem_pressure_deferred_to_cgroup,
    emit_round_cgroup_memory,
    emit_round_deferred,
    emit_round_mem_critical_sample,
    emit_round_mem_terminated,
    emit_round_oom_killed,
    emit_round_resumed,
)
from agent_runner._emit.rounds import (  # noqa: F401 — public re-export
    emit_fresh_eyes_round_triggered,
    emit_round_grace_extended,
    emit_round_grace_kill,
    emit_round_logs_prune_deferred,
    emit_round_progress,
    emit_round_substrate_after,
    emit_round_substrate_before,
    emit_round_supervisor_wedged,
    emit_stale_index_lock_cleared,
)
from agent_runner._emit.serve import (  # noqa: F401 — public re-export
    emit_config_broken,
    emit_config_migrated,
    emit_crash_loop,
    emit_max_rounds_reached,
    emit_mem_loop,
    emit_mem_loop_persistent,
    emit_rate_limit_stop,
    emit_schedule_paused,
    emit_schedule_phase_skipped,
    emit_schedule_resumed,
    emit_stalled_no_progress,
    emit_stop_file_detected,
)

__all__ = [
    "emit_agent_auth_error_detected",
    "emit_agent_usage_recorded",
    "emit_anomaly_repetitive_tool",
    "emit_config_broken",
    "emit_config_migrated",
    "emit_crash_loop",
    "emit_fresh_eyes_round_triggered",
    "emit_host_cgroup_memory_limit",
    "emit_max_rounds_reached",
    "emit_mem_loop",
    "emit_mem_loop_persistent",
    "emit_mem_pressure_deferred_to_cgroup",
    "emit_rate_limit_stop",
    "emit_round_cgroup_memory",
    "emit_round_deferred",
    "emit_round_grace_extended",
    "emit_round_grace_kill",
    "emit_round_logs_prune_deferred",
    "emit_round_mem_critical_sample",
    "emit_round_mem_terminated",
    "emit_round_oom_killed",
    "emit_round_progress",
    "emit_round_resumed",
    "emit_round_substrate_after",
    "emit_round_substrate_before",
    "emit_round_supervisor_wedged",
    "emit_schedule_paused",
    "emit_schedule_phase_skipped",
    "emit_schedule_resumed",
    "emit_stale_index_lock_cleared",
    "emit_stalled_no_progress",
    "emit_stop_file_detected",
    "emit_transient_error_backoff_capped",
    "emit_transient_error_detected",
    "emit_transient_error_recovered",
]
