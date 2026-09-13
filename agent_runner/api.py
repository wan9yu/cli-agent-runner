"""Public Python API mirroring CLI verbs — thin facade.

Every name below is re-exported from its actual home in _install.py /
_lifecycle.py / _observe.py so `from agent_runner.api import X` (external
callers) and `from agent_runner import api; api.X` (internal callers, tests)
keep working unchanged after the split. See those modules for implementation
and docstrings.
"""

from __future__ import annotations

# --- stdlib re-exports: pinned by tests/contract/test_public_api_surface.py's
# exact-equality snapshot of dir(agent_runner.api). Nothing below this line
# uses these directly (the logic that did moved to _install/_lifecycle/
# _observe), they exist purely so the historical public surface is unchanged.
import dataclasses  # noqa: F401 — public re-export (surface pin)
import os  # noqa: F401 — public re-export (surface pin)
import shutil  # noqa: F401 — public re-export (surface pin)
import signal  # noqa: F401 — public re-export (surface pin)
import subprocess  # noqa: F401,TID251 — public re-export (surface pin)
import sysconfig  # noqa: F401 — public re-export (surface pin)
from collections.abc import Iterator, Sequence  # noqa: F401 — public re-export
from pathlib import Path  # noqa: F401 — public re-export
from typing import Any, TextIO  # noqa: F401 — public re-export

from agent_runner import defenses, events, lifecycle, monitor  # noqa: F401 — public re-export

# Re-export emit_* wrappers from _emit module (extracted for size hygiene).
# Preserves the public import surface: `from agent_runner.api import emit_*` continues to work.
from agent_runner._emit import (  # noqa: E402,F401 — intentional bottom re-export
    emit_agent_auth_error_detected,
    emit_agent_usage_recorded,
    emit_anomaly_repetitive_tool,
    emit_cgroup_growth_rate_warning,
    emit_config_broken,
    emit_config_migrated,
    emit_crash_loop,
    emit_fresh_eyes_round_triggered,
    emit_host_cgroup_memory_limit,
    emit_max_rounds_reached,
    emit_mem_loop,
    emit_mem_loop_persistent,
    emit_mem_pressure_deferred_to_cgroup,
    emit_plugin_builtin_name_squat,
    emit_plugin_checksum_mismatch,
    emit_plugin_sandbox_degraded,
    emit_plugin_sandbox_kill,
    emit_rate_limit_stop,
    emit_round_cgroup_memory,
    emit_round_container_orphan_risk,
    emit_round_deferred,
    emit_round_grace_extended,
    emit_round_grace_kill,
    emit_round_logs_prune_deferred,
    emit_round_mem_critical_sample,
    emit_round_mem_terminated,
    emit_round_oom_killed,
    emit_round_progress,
    emit_round_resumed,
    emit_round_substrate_after,
    emit_round_substrate_before,
    emit_round_supervisor_wedged,
    emit_schedule_paused,
    emit_schedule_phase_skipped,
    emit_schedule_resumed,
    emit_stale_index_lock_cleared,
    emit_stalled_no_progress,
    emit_stop_file_detected,
    emit_transient_error_backoff_capped,
    emit_transient_error_detected,
    emit_transient_error_recovered,
)
from agent_runner._install import (  # noqa: F401 — public re-export
    _agent_runner_script_path,
    _install_system,
    init,
    install,
    uninstall,
)
from agent_runner._lifecycle import (  # noqa: F401 — public re-export
    _PID_SIGNAL_GRACE_S,
    _ROUND_TERM_GRACE_S,
    _SYSTEM_UNITS_DIR,
    _await_pid_exit,
    _check_user_systemd_available,
    _log_dir,
    _log_dir_for_project,
    _project_name,
    _resolve_project,
    _resolve_target,
    _round_holder_pid,
    _system_unit_exists,
    _systemctl_is_active,
    _systemctl_user,
    _systemd_active,
    _terminate_round_pid,
    kill,
    outer_round_ceiling_s,
    restart,
    start,
    status,
    stop,
)
from agent_runner._observe import (  # noqa: F401 — public re-export
    _MONITOR_SEEN_CAP,
    _RECENT_BLIPS_LIMIT,
    _RECENT_HOOK_FAILURES_LIMIT,
    _FailOpenGuard,
    _format_narrate_line,
    _monitor_loop_iter,
    _poll_once,
    _recent_events_of_kind,
    monitor_loop,
    narrate_events,
    peek,
    relay_remote_events,
    stream_events_jsonl,
)

# Round-input assembly + sentinel helpers live in _round_support (extracted
# for size hygiene). Re-exported here so `from agent_runner.api
# import assemble_prompt` etc. continue to work -- RuntimeConfig travels along
# with resolve_runtime_for_phase since it's the return type plugin authors
# need to annotate against, and it's itself pinned in EXPECTED_API_SURFACE.
from agent_runner._round_support import (  # noqa: E402,F401 — intentional bottom re-export
    RuntimeConfig,
    assemble_prompt,
    check_self_terminated_sentinel,
    read_round_num,
    read_sentinel_content,
    resolve_runtime_for_phase,
)

# Restart policy lives in _serve_policy (pure, dependency-free). Re-exported
# here so external callers keep `from agent_runner.api import post_round_decision`.
from agent_runner._serve_policy import (  # noqa: F401 — public re-export
    CRASH_LOOP_EXIT,
    CRASH_LOOP_MAX_DELAY_S,
    CRASH_LOOP_SHORT_EXIT_S,
    CRASH_LOOP_THRESHOLD,
    ENV_BATTERY_EXIT,
    MEM_LOOP_EXIT,
    MEM_LOOP_PERSISTENT_EXIT,
    PERMANENT_CONFIG_EXIT,
    post_round_decision,
)
from agent_runner.api_types import (  # noqa: F401 — public re-export
    InitResult,
    InstallResult,
    ProjectState,
    RateLimitState,
    ServiceMode,
    ServiceStatus,
    select_path,
)
from agent_runner.clock import SYSTEM_CLOCK, wait_until  # noqa: F401 — public re-export
from agent_runner.config import Config, load_config  # noqa: F401 — public re-export
from agent_runner.events import (  # noqa: F401 — public re-export
    AGENT_NETWORK_BLIP,
    HOOK_FAILED,
    MONITOR_STARTED,
)
from agent_runner.lifecycle import (  # noqa: F401 — public re-export
    PIDFile,
    detect_service_mode,
    pid_alive,
    send_signal_to_pid,
)
from agent_runner.scaffold import scaffold_project  # noqa: F401 — public re-export
from agent_runner.service_unit import (  # noqa: F401 — public re-export
    monitor_unit_filename,
    render_monitor_unit,
    render_serve_unit,
    serve_unit_filename,
)

# Re-export git primitives so external callers can reach them via the api facade.
# The exception types travel with their functions: a caller catching what
# stash_orphan / try_auto_commit raise imports it from the same place.
from agent_runner.vcs_state import (  # noqa: E402,F401 — public primitives
    AutoCommitError,
    StashError,
    stash_orphan,
    try_auto_commit,
)
