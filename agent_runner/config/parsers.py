"""One ``_parse_*`` per TOML table: turns a raw ``dict`` sub-tree into its
typed ``models.py`` dataclass, raising ``ConfigError`` on any footgun.

``loader.py`` is the only caller — it owns cross-table plumbing (work_dir/
project_name threading, table-as-scalar guards before handing a table dict
here) and stays thin glue over these.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runner import schedule
from agent_runner.config.errors import ConfigError
from agent_runner.config.models import (
    _AGENT_ALLOWED_FIELDS,
    _DEFAULT_AUTH_HINT,
    _DEFAULT_AUTH_PATTERNS,
    _DEFAULT_AUTO_STOP_ON,
    _DEFAULT_REMOTE_FAILURE_TOLERANCE_S,
    _MONITOR_ALLOWED_FIELDS,
    _MONITOR_HOST_HEALTH_ALLOWED_FIELDS,
    _PHASE_OVERRIDE_ALLOWED_FIELDS,
    _PHASE_PROMPT_ALLOWED_FIELDS,
    _PHASE_RUNTIME_ALLOWED_FIELDS,
    _PROMPT_ALLOWED_FIELDS,
    _RUNTIME_ALLOWED_FIELDS,
    _SCHEDULE_ALLOWED_FIELDS,
    _VALID_DIRTY_ACTIONS,
    _VALID_INJECTION_MODES,
    _VALID_PROMPT_DELIVERY,
    _VALID_TRANSIENT_ERROR_ACTIONS,
    _VCS_ALLOWED_FIELDS,
    AgentConfig,
    MonitorConfig,
    MonitorHostHealthConfig,
    PhaseOverride,
    PluginsConfig,
    PromptConfig,
    RuntimeConfig,
    ScheduleConfig,
    VcsConfig,
)
from agent_runner.config.validators import (
    _expand_and_resolve,
    _reject_control_chars,
    _reject_unknown_fields,
    _require,
    _require_bool,
    _require_non_negative_int,
    _require_pct,
    _require_positive_int,
    _require_str_list,
    _require_table,
    _validate_regex_list,
)


def _parse_agent(
    agent_d: dict, *, field_prefix: str, require_prompt_placeholder: bool = True
) -> AgentConfig:
    """Parse + validate an [agent] table (or a merged per-phase agent table).

    ``field_prefix`` names the table in error messages (e.g. ``"[agent]"`` or
    ``"[phases.b.agent]"``). The prompt_delivery validity check and the
    stdin/``{prompt}`` cross-check run here so a per-phase agent override is
    validated on the MERGED result, not just the base [agent] table.

    ``require_prompt_placeholder`` gates the argv-``{prompt}``-token check for
    the phase carve-out: a phase whose own ``prompt.files = []`` sends no
    prompt at all, so its own argv template legitimately needs no ``{prompt}``
    token. The base ``[agent]`` table always requires one (a top-level prompt
    is mandatory and non-empty) — callers other than the per-phase resolver
    leave this at its default.
    """
    _reject_unknown_fields(agent_d, _AGENT_ALLOWED_FIELDS, field_prefix.strip("[]"))
    prompt_delivery = str(agent_d.get("prompt_delivery", "argv"))
    prompt_arg_template = _require_str_list(
        _require(agent_d, "prompt_arg_template"), field=f"{field_prefix} prompt_arg_template"
    )
    if prompt_delivery not in _VALID_PROMPT_DELIVERY:
        raise ConfigError(
            f'invalid {field_prefix} prompt_delivery {prompt_delivery!r}: use "argv" or "stdin"'
        )
    if prompt_delivery == "stdin" and any("{prompt}" in a for a in prompt_arg_template):
        raise ConfigError(
            f"stdin delivery: remove {{prompt}} from {field_prefix} prompt_arg_template "
            "(the prompt is piped to stdin, not placed in argv)"
        )
    if (
        prompt_delivery == "argv"
        and require_prompt_placeholder
        and not any("{prompt}" in a for a in prompt_arg_template)
    ):
        raise ConfigError(
            f"{field_prefix} prompt_arg_template has no {{prompt}} placeholder: the "
            "prompt is never delivered to the agent; add {prompt} to one of the argv "
            "tokens (or, for a per-phase override, set that phase's prompt.files = [] "
            "if it truly sends no prompt); run `agent-runner migrate`"
        )
    command = _require_str_list(_require(agent_d, "command"), field=f"{field_prefix} command")
    if not command:
        raise ConfigError(
            f"{field_prefix} command: must be a non-empty list; run `agent-runner migrate`"
        )
    env_d = _require_table(agent_d, "env", label=f"{field_prefix.strip('[]')}.env")
    return AgentConfig(
        command=command,
        prompt_arg_template=prompt_arg_template,
        name=agent_d.get("name"),
        env={str(k): str(v) for k, v in env_d.items()},
        prompt_delivery=prompt_delivery,  # type: ignore[arg-type]  # narrowed above
    )


def _parse_phase_overrides(
    phases_d: dict[str, Any],
    phases_list: list[str] | None,
    project_name: str,
    *,
    work_dir: Path,
    agent_d: dict[str, Any],
) -> dict[str, PhaseOverride]:
    """Parse [phases.<name>] sub-tables from raw TOML dict.

    Each sub-table is keyed by phase name (must appear in phases.list). Allowed
    fields are validated; unknown fields raise. ``agent_d`` is the base [agent]
    table — a per-phase ``agent`` sub-table field-merges onto it and the merged
    result is validated. Returns {phase_name: PhaseOverride}.
    """
    overrides: dict[str, PhaseOverride] = {}
    for key, value in phases_d.items():
        if key in ("list", "phase_policy"):
            continue
        if not isinstance(value, dict):
            raise ConfigError(
                f"[phases] {key!r} must be a phase sub-table ([phases.{key}]), got "
                f"{type(value).__name__}; only 'list'/'phase_policy' are scalar [phases] "
                f"fields. Run `agent-runner migrate`."
            )
        phase_name = key
        if phases_list is None or phase_name not in phases_list:
            raise ConfigError(
                f"[phases.{phase_name}] declared but {phase_name!r} not in phases.list "
                f"({phases_list})"
            )
        unknown = set(value.keys()) - _PHASE_OVERRIDE_ALLOWED_FIELDS
        if unknown:
            raise ConfigError(
                f"unknown per-phase field(s) under [phases.{phase_name}]: {sorted(unknown)}; "
                f"allowed: round_timeout_s, disable_pre_round_hooks, prompt.files, "
                f"agent, runtime, schedule"
            )

        # runtime: flat aliases (round_timeout_s/disable_pre_round_hooks) and/or
        # a nested [phases.<name>.runtime] sub-table. Setting both twins raises.
        runtime_sub = value.get("runtime")
        if runtime_sub is not None and not isinstance(runtime_sub, dict):
            raise ConfigError(f"[phases.{phase_name}.runtime] must be a table")
        runtime_sub = runtime_sub or {}
        unknown_rt = set(runtime_sub.keys()) - _PHASE_RUNTIME_ALLOWED_FIELDS
        if unknown_rt:
            raise ConfigError(
                f"unknown field(s) under [phases.{phase_name}.runtime]: {sorted(unknown_rt)}; "
                f"allowed: round_timeout_s, disable_pre_round_hooks"
            )
        for fld in _PHASE_RUNTIME_ALLOWED_FIELDS:
            if fld in value and fld in runtime_sub:
                raise ConfigError(
                    f"[phases.{phase_name}] set both the flat field and "
                    f"[phases.{phase_name}.runtime].{fld}; use one"
                )

        # A field resolves from the nested [...runtime] sub-table, else the flat
        # alias (the twin case already raised above), so nested-first is safe.
        round_timeout_s = None
        if "round_timeout_s" in runtime_sub:
            round_timeout_s = _require_positive_int(
                runtime_sub["round_timeout_s"], field=f"phases.{phase_name}.runtime.round_timeout_s"
            )
        elif "round_timeout_s" in value:
            round_timeout_s = _require_positive_int(
                value["round_timeout_s"], field=f"phases.{phase_name}.round_timeout_s"
            )
        disable_hooks = None
        if "disable_pre_round_hooks" in runtime_sub:
            disable_hooks = _require_bool(
                runtime_sub["disable_pre_round_hooks"],
                field=f"phases.{phase_name}.runtime.disable_pre_round_hooks",
            )
        elif "disable_pre_round_hooks" in value:
            disable_hooks = _require_bool(
                value["disable_pre_round_hooks"],
                field=f"phases.{phase_name}.disable_pre_round_hooks",
            )

        prompt_files = None
        if "prompt" in value:
            prompt_sub = value["prompt"]
            if not isinstance(prompt_sub, dict) or "files" not in prompt_sub:
                raise ConfigError(f"[phases.{phase_name}].prompt must have a 'files' list")
            _reject_unknown_fields(
                prompt_sub, _PHASE_PROMPT_ALLOWED_FIELDS, f"phases.{phase_name}.prompt"
            )
            files_raw = _require_str_list(
                prompt_sub["files"], field=f"phases.{phase_name}.prompt.files"
            )
            prompt_files = [_expand_and_resolve(p, project_name, work_dir) for p in files_raw]

        phase_agent = None
        if "agent" in value:
            agent_sub = value["agent"]
            if not isinstance(agent_sub, dict):
                raise ConfigError(f"[phases.{phase_name}.agent] must be a table")
            merged = {**agent_d, **agent_sub}
            phase_agent = _parse_agent(
                merged,
                field_prefix=f"[phases.{phase_name}.agent]",
                require_prompt_placeholder=(prompt_files != []),
            )

        phase_schedule = None
        if "schedule" in value:
            sched_sub = value["schedule"]
            if not isinstance(sched_sub, dict):
                raise ConfigError(f"[phases.{phase_name}.schedule] must be a table")
            phase_schedule = _parse_schedule(sched_sub, label=f"phases.{phase_name}.schedule")

        overrides[phase_name] = PhaseOverride(
            round_timeout_s=round_timeout_s,
            disable_pre_round_hooks=disable_hooks,
            prompt_files=prompt_files,
            agent=phase_agent,
            schedule=phase_schedule,
        )
    return overrides


def _parse_substrate_fingerprint_paths(runtime_d: dict) -> list[str]:
    raw = runtime_d.get("substrate_fingerprint_paths", [])
    if not isinstance(raw, list):
        raise ConfigError("runtime.substrate_fingerprint_paths: must be list of glob strings")
    return [str(p) for p in raw]


def _parse_fresh_eyes_every_n(runtime_d: dict) -> int | None:
    raw = runtime_d.get("fresh_eyes_every_n")
    if raw is None:
        return None
    return _require_positive_int(raw, field="runtime.fresh_eyes_every_n")


def _parse_schedule(schedule_d: dict, *, label: str = "schedule") -> ScheduleConfig:
    """Parse + validate a ``[schedule]`` table (or a per-phase
    ``[phases.<name>.schedule]`` sub-table).

    ``label`` names the table in every error message without its own
    brackets (default ``"schedule"`` for the top-level table) — a per-phase
    caller passes ``f"phases.{name}.schedule"`` so a bad key reports the
    phase it actually came from, not a blanket ``[schedule]``.
    """
    _reject_unknown_fields(schedule_d, _SCHEDULE_ALLOWED_FIELDS, label)
    tz = schedule_d.get("timezone")
    if tz is not None:
        tz = str(tz)
        if not schedule.valid_timezone(tz):
            raise ConfigError(f"{label}.timezone: unknown IANA zone {tz!r}")

    def _parse_list(key: str) -> tuple[schedule.Window, ...]:
        raw = schedule_d.get(key, [])
        if not isinstance(raw, list):
            raise ConfigError(f"{label}.{key} must be a list of window strings")
        out = []
        for w in raw:
            try:
                out.append(schedule.parse_window(str(w)))
            except ValueError as e:
                raise ConfigError(f"{label}.{key}: {e}") from e
        return tuple(out)

    return ScheduleConfig(
        timezone=tz,
        run_windows=_parse_list("run_windows"),
        pause_windows=_parse_list("pause_windows"),
    )


def _parse_runtime(runtime_d: dict, *, project_name: str, work_dir: Path) -> RuntimeConfig:
    """Parse + validate the ``[runtime]`` table into a ``RuntimeConfig``.

    ``work_dir`` is already resolved by the caller (needed earlier for phases
    parsing too); ``project_name`` drives ``{project}``-template expansion in
    path fields.
    """
    if "round_timeout_per_phase" in runtime_d:
        raise ConfigError(
            "runtime.round_timeout_per_phase removed in 0.1.16; "
            "use [phases.<name>] round_timeout_s = X. Run `agent-runner migrate`."
        )

    if runtime_d.get("rate_limit_action") is not None:
        raise ConfigError(
            "runtime.rate_limit_action was removed in 0.1.29; use "
            "runtime.transient_error_action (same allowed values: "
            "back_off / skip / stop). Run `agent-runner migrate`."
        )

    _reject_unknown_fields(runtime_d, _RUNTIME_ALLOWED_FIELDS, "runtime")

    transient_error_action_raw = runtime_d.get("transient_error_action")
    transient_error_action = str(
        transient_error_action_raw if transient_error_action_raw is not None else "back_off"
    )
    if transient_error_action not in _VALID_TRANSIENT_ERROR_ACTIONS:
        raise ConfigError(
            f"runtime.transient_error_action: {transient_error_action!r} not in allowed values "
            f"{sorted(_VALID_TRANSIENT_ERROR_ACTIONS)}"
        )

    raw_log_dir = str(_require(runtime_d, "log_dir"))
    # Same raw-string-first ordering as work_dir in load_config -- a relative
    # log_dir goes through Path.resolve() inside _expand_and_resolve, which
    # would raise a bare ValueError on a NUL byte before the post-resolve
    # check below ever ran.
    _reject_control_chars(raw_log_dir, "runtime.log_dir")
    log_dir = _expand_and_resolve(raw_log_dir, project_name, work_dir)
    _reject_control_chars(str(log_dir), "runtime.log_dir")

    return RuntimeConfig(
        work_dir=work_dir,
        log_dir=log_dir,
        round_timeout_s=_require_positive_int(
            runtime_d.get("round_timeout_s", 1800), field="runtime.round_timeout_s"
        ),
        restart_delay_s=_require_positive_int(
            runtime_d.get("restart_delay_s", 3), field="runtime.restart_delay_s"
        ),
        disable_pre_round_hooks=_require_bool(
            runtime_d.get("disable_pre_round_hooks", False),
            field="runtime.disable_pre_round_hooks",
        ),
        round_log_retention=_require_non_negative_int(
            runtime_d.get("round_log_retention", 0), field="runtime.round_log_retention"
        ),
        narrative_file=_expand_and_resolve(str(runtime_d["narrative_file"]), project_name, work_dir)
        if "narrative_file" in runtime_d
        else None,
        transient_error_action=transient_error_action,  # type: ignore[arg-type]
        max_rounds=_require_positive_int(runtime_d["max_rounds"], field="runtime.max_rounds")
        if "max_rounds" in runtime_d
        else None,
        stop_file=_expand_and_resolve(str(runtime_d["stop_file"]), project_name, work_dir)
        if "stop_file" in runtime_d
        else None,
        substrate_fingerprint_paths=_parse_substrate_fingerprint_paths(runtime_d),
        fresh_eyes_every_n=_parse_fresh_eyes_every_n(runtime_d),
        dry_run=_require_bool(
            runtime_d.get("dry_run", False),
            field="runtime.dry_run",
        ),
        max_grace_after_result_s=_require_non_negative_int(
            runtime_d.get("max_grace_after_result_s", 0),
            field="runtime.max_grace_after_result_s",
        ),
        grace_kill_ignore_patterns=_validate_regex_list(
            runtime_d.get("grace_kill_ignore_patterns", []),
            field="runtime.grace_kill_ignore_patterns",
        ),
    )


def _parse_prompt(prompt_d: dict, *, project_name: str, work_dir: Path) -> PromptConfig:
    """Parse + validate the top-level ``[prompt]`` table into a ``PromptConfig``."""
    _reject_unknown_fields(prompt_d, _PROMPT_ALLOWED_FIELDS, "prompt")
    mode = prompt_d.get("context_injection_mode", "prepend")
    if mode not in _VALID_INJECTION_MODES:
        raise ConfigError(
            f"prompt.context_injection_mode must be one of {sorted(_VALID_INJECTION_MODES)}, "
            f"got {mode!r}"
        )
    has_file = "file" in prompt_d
    has_files = "files" in prompt_d
    if has_file and has_files:
        raise ConfigError("set either prompt.file or prompt.files, not both")
    if not has_file and not has_files:
        raise ConfigError("missing required field: prompt.file or prompt.files")
    prompt_file = (
        _expand_and_resolve(str(prompt_d["file"]), project_name, work_dir) if has_file else None
    )
    if has_files:
        files_raw = _require_str_list(prompt_d["files"], field="prompt.files")
        if not files_raw:
            raise ConfigError(
                "prompt.files: must be a non-empty list (use a per-phase "
                "[phases.<name>.prompt] files = [] to disable prompts for a phase); "
                "run `agent-runner migrate`"
            )
        prompt_files = [_expand_and_resolve(p, project_name, work_dir) for p in files_raw]
    else:
        prompt_files = []
    return PromptConfig(
        file=prompt_file,
        files=prompt_files,
        inject_context=_require_bool(
            prompt_d.get("inject_context", True), field="prompt.inject_context"
        ),
        context_injection_mode=mode,  # type: ignore[arg-type]  # narrowed by validation above
        concat_separator=str(prompt_d.get("concat_separator", "\n\n")),
        strip_yaml_frontmatter=_require_bool(
            prompt_d.get("strip_yaml_frontmatter", True),
            field="prompt.strip_yaml_frontmatter",
        ),
    )


def _parse_vcs(vcs_d: dict) -> VcsConfig:
    """Parse + validate the ``[vcs]`` table into a ``VcsConfig``."""
    if "orphan_action" in vcs_d:
        raise ConfigError(
            "vcs.orphan_action removed in 0.1.18; use vcs.dirty_action. Run `agent-runner migrate`."
        )
    _reject_unknown_fields(vcs_d, _VCS_ALLOWED_FIELDS, "vcs")
    dirty_action = str(vcs_d.get("dirty_action", "stash"))
    if dirty_action not in _VALID_DIRTY_ACTIONS:
        raise ConfigError(
            f"vcs.dirty_action: {dirty_action!r} not in allowed values "
            f"{{'stash', 'ignore', 'auto_commit'}}"
        )
    return VcsConfig(
        stash_idempotency_s=_require_positive_int(
            vcs_d.get("stash_idempotency_s", 5), field="vcs.stash_idempotency_s"
        ),
        dirty_action=dirty_action,
    )


def _validate_remote_failure_tolerance(value: Any) -> int:
    """Validate monitor.remote_failure_tolerance_s: int in [0, 3600]."""
    v = _require_non_negative_int(value, field="monitor.remote_failure_tolerance_s")
    if v > 3600:
        raise ConfigError(f"monitor.remote_failure_tolerance_s: must be <= 3600, got {v}")
    return v


def _parse_monitor(monitor_d: dict) -> MonitorConfig:
    """Parse + validate the ``[monitor]`` table (incl. nested
    ``[monitor.host_health]``) into a ``MonitorConfig``."""
    _reject_unknown_fields(monitor_d, _MONITOR_ALLOWED_FIELDS, "monitor")
    hh_d = _require_table(monitor_d, "host_health", label="monitor.host_health")
    _reject_unknown_fields(hh_d, _MONITOR_HOST_HEALTH_ALLOWED_FIELDS, "monitor.host_health")
    host_health = MonitorHostHealthConfig(
        mem_avail_min_mb=_require_non_negative_int(
            hh_d.get("mem_avail_min_mb", 200),
            field="monitor.host_health.mem_avail_min_mb",
        ),
        disk_warning_pct=_require_pct(
            hh_d.get("disk_warning_pct", 90.0),
            field="monitor.host_health.disk_warning_pct",
        ),
        disk_critical_pct=_require_pct(
            hh_d.get("disk_critical_pct", 95.0),
            field="monitor.host_health.disk_critical_pct",
        ),
        swap_sout_noise_floor_mb=_require_positive_int(
            hh_d.get("swap_sout_noise_floor_mb", 32),
            field="monitor.host_health.swap_sout_noise_floor_mb",
        ),
        mem_free_low_mb=_require_positive_int(
            hh_d.get("mem_free_low_mb", 16),
            field="monitor.host_health.mem_free_low_mb",
        ),
    )
    monitor = MonitorConfig(
        auth_fail_patterns=_validate_regex_list(
            monitor_d.get("auth_fail_patterns", _DEFAULT_AUTH_PATTERNS),
            field="monitor.auth_fail_patterns",
        ),
        auth_fail_hint=str(monitor_d.get("auth_fail_hint", _DEFAULT_AUTH_HINT)),
        auto_stop_on=(
            list(_DEFAULT_AUTO_STOP_ON)
            if "auto_stop_on" not in monitor_d
            else _require_str_list(monitor_d["auto_stop_on"], field="monitor.auto_stop_on")
        ),
        remote_failure_tolerance_s=_validate_remote_failure_tolerance(
            monitor_d.get("remote_failure_tolerance_s", _DEFAULT_REMOTE_FAILURE_TOLERANCE_S),
        ),
        anomaly_repetitive_window=_require_non_negative_int(
            monitor_d.get("anomaly_repetitive_window", 0),
            field="monitor.anomaly_repetitive_window",
        ),
        anomaly_repetitive_threshold=_require_non_negative_int(
            monitor_d.get("anomaly_repetitive_threshold", 0),
            field="monitor.anomaly_repetitive_threshold",
        ),
        host_health=host_health,
        round_progress_interval_s=_require_non_negative_int(
            monitor_d.get("round_progress_interval_s", 0),
            field="monitor.round_progress_interval_s",
        ),
        supervisor_stale_threshold_s=(
            None
            if monitor_d.get("supervisor_stale_threshold_s") is None
            else _require_non_negative_int(
                monitor_d["supervisor_stale_threshold_s"],
                field="monitor.supervisor_stale_threshold_s",
            )
        ),
    )
    if (
        monitor.anomaly_repetitive_threshold > 0
        and monitor.anomaly_repetitive_window > 0
        and monitor.anomaly_repetitive_threshold > monitor.anomaly_repetitive_window
    ):
        raise ConfigError(
            f"monitor.anomaly_repetitive_threshold ({monitor.anomaly_repetitive_threshold}) "
            f"must be <= anomaly_repetitive_window ({monitor.anomaly_repetitive_window}): "
            f"the detector can never fire otherwise; run `agent-runner migrate`"
        )
    return monitor


def _parse_plugins(plugins_d: dict) -> PluginsConfig:
    """Parse the ``[plugins]`` table into a ``PluginsConfig``.

    Known keys (``disable``) are popped into first-class fields; whatever
    remains lands in ``.raw`` for plugin-author-defined sub-keys.
    """
    plugins_raw = dict(plugins_d)  # copy so we can pop
    disable = (
        _require_str_list(plugins_raw.pop("disable"), field="plugins.disable")
        if "disable" in plugins_raw
        else []
    )
    return PluginsConfig(disable=disable, raw=plugins_raw)
