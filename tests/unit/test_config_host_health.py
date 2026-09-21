from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import ConfigError, load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text("schema_version = 1\n" + body)
    return p


_MINIMAL_TOML_NO_PLUGINS = """\
[agent]
command = ["true"]
prompt_arg_template = ["{{prompt}}"]
[runtime]
work_dir = "{tmp_path}"
log_dir = "{tmp_path}/logs"
[prompt]
file = "{tmp_path}/prompt.md"
"""


def test_host_health_config_should_default_brake_off_and_nudge_off_when_table_absent() -> None:
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.brake.memory_high is False
    assert cfg.brake.memory_high_step_pct == 0  # cap-at-current: no reclaim burst by default
    assert cfg.brake.warning_consecutive_samples == 3
    assert cfg.pressure.in_round_nudge is False


def test_load_config_should_parse_grouped_host_health_tables_when_present(
    tmp_path: Path,
) -> None:
    from tests._test_helpers import make_toml_with_sections

    toml = make_toml_with_sections(
        tmp_path,
        runtime_extra=(
            "[monitor.host_health.disk]\n"
            "warning_pct = 80.0\n"
            "[monitor.host_health.memory]\n"
            "avail_min_mb = 100\n"
            "[monitor.host_health.pressure]\n"
            "in_round_terminate = false\n"
            "in_round_nudge = true\n"
            "[monitor.host_health.brake]\n"
            "memory_high = true\n"
            "memory_high_step_pct = 20\n"
            "warning_consecutive_samples = 2\n"
        ),
    )

    cfg = load_config(toml)

    assert cfg.monitor.host_health.disk.warning_pct == 80.0
    assert cfg.monitor.host_health.memory.avail_min_mb == 100
    assert cfg.monitor.host_health.pressure.in_round_terminate is False
    assert cfg.monitor.host_health.disk.critical_pct == 95.0  # untouched default
    assert cfg.monitor.host_health.pressure.in_round_nudge is True
    assert cfg.monitor.host_health.brake.memory_high is True
    assert cfg.monitor.host_health.brake.memory_high_step_pct == 20
    assert cfg.monitor.host_health.brake.warning_consecutive_samples == 2


def test_monitor_host_health_defaults_should_match_detector_defaults_when_invoked(
    tmp_path: Path,
) -> None:
    """Regression: MonitorHostHealthConfig defaults must match detector hardcoded thresholds."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.memory.avail_min_mb == 200
    assert cfg.disk.warning_pct == 90.0
    assert cfg.disk.critical_pct == 95.0


def test_monitor_host_health_toml_section_should_apply_overrides_when_loaded(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand = ["claude"]\nname = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        f'[runtime]\nwork_dir = "."\nlog_dir = "{tmp_path}/logs"\n\n'
        f'[prompt]\nfile = "{prompt_file}"\n\n'
        "[monitor.host_health.memory]\navail_min_mb = 1000\n"
        "[monitor.host_health.disk]\nwarning_pct = 85.0\n",
        encoding="utf-8",
    )

    cfg = load_config(toml)

    assert cfg.monitor.host_health.memory.avail_min_mb == 1000
    assert cfg.monitor.host_health.disk.warning_pct == 85.0
    assert cfg.monitor.host_health.disk.critical_pct == 95.0  # still default


def test_host_health_floor_defaults_should_match_hardcoded_constants_when_invoked() -> None:
    """Defaults must stay byte-identical to the previous hardcoded constants
    (32 MiB swap-out noise floor, 16 MB MemFree floor) -- existing deployments
    are unaffected unless the operator explicitly sets these fields."""
    from agent_runner.config import MonitorHostHealthConfig

    hh = MonitorHostHealthConfig()

    assert hh.memory.swap_out_noise_floor_mb == 32
    assert hh.memory.free_low_mb == 16


def test_host_health_floors_should_parse_from_toml_when_invoked(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand = ["claude"]\nname = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        f'[runtime]\nwork_dir = "."\nlog_dir = "{tmp_path}/logs"\n\n'
        f'[prompt]\nfile = "{prompt_file}"\n\n'
        "[monitor.host_health.memory]\nswap_out_noise_floor_mb = 8\nfree_low_mb = 4\n",
        encoding="utf-8",
    )

    cfg = load_config(toml)

    assert cfg.monitor.host_health.memory.swap_out_noise_floor_mb == 8
    assert cfg.monitor.host_health.memory.free_low_mb == 4


@pytest.mark.parametrize("field", ["swap_out_noise_floor_mb", "free_low_mb"])
def test_host_health_floors_should_reject_zero_and_non_int_values_when_invoked(
    tmp_path: Path, field: str
) -> None:
    """0 would silently disable/invert the floor (delta > 0 is always true) --
    must be rejected via _require_positive_int, not accepted as a no-op."""

    toml_zero = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        f"[monitor.host_health.memory]\n{field} = 0\n",
    )

    with pytest.raises(ConfigError, match=f"monitor.host_health.memory.{field}"):
        load_config(toml_zero)

    toml_str = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        f'[monitor.host_health.memory]\n{field} = "x"\n',
    )

    with pytest.raises(ConfigError, match=f"monitor.host_health.memory.{field}"):
        load_config(toml_str)


def test_custom_mem_threshold_in_config_should_be_used_when_detect_mem_pressure_runs(
    tmp_path: Path,
) -> None:
    from agent_runner.config import (
        MonitorConfig,
        MonitorHostHealthConfig,
        _HostHealthMemoryConfig,
    )
    from agent_runner.monitor import detect_mem_pressure

    host_health = MonitorHostHealthConfig(memory=_HostHealthMemoryConfig(avail_min_mb=500))
    monitor_cfg = MonitorConfig(host_health=host_health)
    metrics = [{"mem_available_mb": 300, "mem_free_mb": 5}]

    alert = detect_mem_pressure(metrics, cfg=monitor_cfg.host_health)

    assert alert is not None
    assert alert.detector == "mem_pressure"


def test_host_health_overrides_should_apply_thresholds_when_run_all_detectors_runs(
    tmp_path: Path,
) -> None:
    """Regression: run_all_detectors must plumb host_health thresholds to detectors.

    Pre-fix, the config was defined but never passed into run_all_detectors, so the
    TOML override silently no-op'd in production. This test exercises the wired path.
    """
    from agent_runner.config import (
        MonitorHostHealthConfig,
        _HostHealthDiskConfig,
        _HostHealthMemoryConfig,
    )
    from agent_runner.monitor import run_all_detectors

    # mem_available_mb=300: below custom avail_min_mb=500 but above default 200;
    # mem_free_mb=5 (low) makes it a genuine combined-low signal, not silence.
    metrics = [{"mem_available_mb": 300, "mem_free_mb": 5, "disk_used_pct": 92.0}]

    alerts = run_all_detectors(
        events=[],
        metrics=metrics,
        log_tails={},
        host_health_cfg=MonitorHostHealthConfig(
            memory=_HostHealthMemoryConfig(avail_min_mb=500),
            disk=_HostHealthDiskConfig(warning_pct=85.0, critical_pct=95.0),
        ),
    )

    kinds = {a.detector for a in alerts}
    assert "mem_pressure" in kinds  # 300 < 500
    assert "disk_warning" in kinds  # 92 in [85, 95)

    # With defaults, neither would fire at these values
    alerts_default = run_all_detectors(events=[], metrics=metrics, log_tails={})

    kinds_default = {a.detector for a in alerts_default}
    assert "mem_pressure" not in kinds_default  # 300 > default 200
    assert "disk_warning" in kinds_default  # 92 still > default 90


def test_run_all_detectors_should_thread_custom_floors_into_config_when_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: run_all_detectors must plumb swap_out_noise_floor_mb and
    free_low_mb into the MonitorHostHealthConfig it builds and passes to
    detect_mem_pressure -- pre-fix these floors were config-tunable in TOML but
    silently ignored on this path (mirrors the avail_min_mb/disk_* wiring
    regression test above)."""
    from agent_runner import host_health, monitor
    from agent_runner.config import MonitorHostHealthConfig, _HostHealthMemoryConfig

    seen: dict[str, int] = {}
    real = host_health.memory_pressure

    def spy(sample, prev, cfg):
        seen["swap_floor"] = cfg.memory.swap_out_noise_floor_mb
        seen["mem_free"] = cfg.memory.free_low_mb
        return real(sample, prev, cfg)

    monkeypatch.setattr(host_health, "memory_pressure", spy)
    metrics = [{"mem_free_mb": 500, "mem_available_mb": 500, "swap_sout": 0}]

    monitor.run_all_detectors(
        events=[],
        metrics=metrics,
        log_tails={},
        host_health_cfg=MonitorHostHealthConfig(
            memory=_HostHealthMemoryConfig(swap_out_noise_floor_mb=8, free_low_mb=4)
        ),
    )

    assert seen == {"swap_floor": 8, "mem_free": 4}


def test_high_disk_critical_should_still_fire_warning_when_disk_used_below_critical(
    tmp_path: Path,
) -> None:
    """detect_disk_warning's upper bound must scale with disk_critical_pct.

    Pre-fix: hardcoded `val >= 95.0` masked warnings at 96–98% when critical was 98%.
    """
    from agent_runner.monitor import detect_disk_warning

    metrics = [{"disk_used_pct": 96.0}]

    alert = detect_disk_warning(metrics, threshold_pct=90.0, critical_pct=98.0)

    assert alert is not None
    assert alert.detector == "disk_warning"


def test_no_supervisor_stale_field_should_default_to_none_when_loaded(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        '[runtime]\nwork_dir = "."\nlog_dir = "/tmp/logs"\n'
        '[prompt]\nfile = "p.md"\n',
    )

    cfg = load_config(toml)

    assert cfg.monitor.supervisor_stale_threshold_s is None


def test_supervisor_stale_threshold_should_be_loaded_when_set(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        '[runtime]\nwork_dir = "."\nlog_dir = "/tmp/logs"\n'
        '[prompt]\nfile = "p.md"\n'
        "[monitor]\nsupervisor_stale_threshold_s = 600\n",
    )

    cfg = load_config(toml)

    assert cfg.monitor.supervisor_stale_threshold_s == 600


def test_grace_kill_ignore_patterns_should_default_to_empty_when_invoked(tmp_path: Path) -> None:
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        '[prompt]\nfile="p.md"\n'
    )

    cfg = load_config(toml)

    assert cfg.runtime.grace_kill_ignore_patterns == []


def test_grace_kill_ignore_patterns_should_be_parsed_from_toml_when_invoked(tmp_path: Path) -> None:
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        "grace_kill_ignore_patterns = ['\\.claude/shell-snapshots/']\n"
        '[prompt]\nfile="p.md"\n'
    )

    cfg = load_config(toml)

    assert cfg.runtime.grace_kill_ignore_patterns == [r"\.claude/shell-snapshots/"]


def test_grace_kill_ignore_patterns_should_raise_when_regex_invalid(tmp_path: Path) -> None:
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        'grace_kill_ignore_patterns = ["[unclosed"]\n'
        '[prompt]\nfile="p.md"\n'
    )

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert "grace_kill_ignore_patterns" in str(exc.value)


_HOST_HEALTH_DISK_BASE = """\
[agent]
command = ["true"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "."
log_dir = "logs"
[prompt]
file = "p.md"
[monitor.host_health.disk]
"""

_HOST_HEALTH_PRESSURE_BASE = _HOST_HEALTH_DISK_BASE.replace(
    "[monitor.host_health.disk]", "[monitor.host_health.pressure]"
)


@pytest.mark.parametrize("field", ["warning_pct", "critical_pct"])
@pytest.mark.parametrize(
    "literal",
    [
        "true",  # bool is an int subclass — must be rejected before the numeric check
        '"95"',
        "500.0",
        "-5.0",
        "nan",
        "inf",
    ],
)
def test_host_health_pct_field_should_raise_when_value_invalid(
    tmp_path: Path, field: str, literal: str
) -> None:
    """A percent threshold outside [0, 100] silently disables its detector —
    disk_critical carries auto_action='stop_service'. Both fields are parametrised:
    guarding only one lets the other's validation be deleted with the suite green."""
    toml = _write_toml(tmp_path, _HOST_HEALTH_DISK_BASE + f"{field} = {literal}\n")

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert f"monitor.host_health.disk.{field}" in str(exc.value)


@pytest.mark.parametrize("field", ["warning_pct", "critical_pct"])
@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("95.0", 95.0),
        ("90", 90.0),  # TOML parses a bare int; both int and float literals accepted
        ("0", 0.0),
        ("100", 100.0),
    ],
)
def test_host_health_pct_field_should_accept_valid_values_when_invoked(
    tmp_path: Path, field: str, literal: str, expected: float
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_DISK_BASE + f"{field} = {literal}\n")

    assert getattr(load_config(toml).monitor.host_health.disk, field) == expected


def test_host_health_defaults_should_be_used_when_section_absent(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_DISK_BASE)

    cfg = load_config(toml)

    assert cfg.monitor.host_health.disk.warning_pct == 90.0
    assert cfg.monitor.host_health.disk.critical_pct == 95.0


def test_psi_thresholds_should_have_expected_defaults_when_invoked() -> None:
    """Default critical raised 1.0 -> 60.0 (0.2.15's 1% hiccup killed every
    round on a 462MB Pi; 60 matches systemd-oomd's DefaultMemoryPressureLimit,
    coma-onset rather than a swap hiccup)."""
    from agent_runner.config import MonitorHostHealthConfig

    assert MonitorHostHealthConfig().pressure.full_avg10_critical == 60.0
    assert MonitorHostHealthConfig().pressure.some_avg10_warning == 5.0


def test_psi_thresholds_should_apply_custom_critical_override_when_set(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + "full_avg10_critical = 75\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.pressure.full_avg10_critical == 75.0
    assert cfg.monitor.host_health.pressure.some_avg10_warning == 5.0  # still default


@pytest.mark.parametrize("field", ["full_avg10_critical", "some_avg10_warning"])
@pytest.mark.parametrize("bad", ["0", "101", '"x"'])
def test_host_health_psi_threshold_should_reject_out_of_range_value_when_invoked(
    tmp_path: Path, field: str, bad: str
) -> None:
    """0 is rejected (not just accepted-as-boundary like the disk pct fields):
    a PSI threshold of 0 fires on any measurable reading (psi_full/some >= 0
    is always true) -- the same hiccup-not-coma footgun this release exists
    to fix, at the opposite extreme. See _require_positive_pct."""
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"{field} = {bad}\n")

    with pytest.raises(ValueError, match=f"monitor.host_health.pressure.{field}"):
        load_config(toml)


@pytest.mark.parametrize("field", ["full_avg10_critical", "some_avg10_warning"])
@pytest.mark.parametrize(("literal", "expected"), [("100", 100.0), ("0.5", 0.5), ("60", 60.0)])
def test_host_health_psi_threshold_should_accept_in_range_value_when_invoked(
    tmp_path: Path, field: str, literal: str, expected: float
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"{field} = {literal}\n")

    assert getattr(load_config(toml).monitor.host_health.pressure, field) == expected


def test_mid_round_hysteresis_should_default_to_three_samples_terminate_on_when_invoked() -> None:
    """Defaults: 3 consecutive critical samples required, off switch defaults on
    (existing deployments keep terminating, just with hysteresis now applied)."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.pressure.critical_consecutive_samples == 3
    assert cfg.pressure.in_round_terminate is True


def test_critical_consecutive_samples_should_parse_from_toml_when_invoked(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + "critical_consecutive_samples = 5\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.pressure.critical_consecutive_samples == 5


@pytest.mark.parametrize("bad", ["0", "-1", '"x"', "true"])
def test_critical_consecutive_samples_should_reject_invalid_values_when_invoked(
    tmp_path: Path, bad: str
) -> None:
    """Must be a positive int -- 0 or negative would terminate on the very
    first (or never) sample, defeating the hysteresis this field exists for."""
    toml = _write_toml(
        tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"critical_consecutive_samples = {bad}\n"
    )

    with pytest.raises(
        ValueError, match="monitor.host_health.pressure.critical_consecutive_samples"
    ):
        load_config(toml)


@pytest.mark.parametrize(("literal", "expected"), [("true", True), ("false", False)])
def test_in_round_terminate_should_parse_bool_value_when_invoked(
    tmp_path: Path, literal: str, expected: bool
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"in_round_terminate = {literal}\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.pressure.in_round_terminate is expected


@pytest.mark.parametrize("bad", ["1", '"true"'])
def test_in_round_terminate_should_reject_non_bool_value_when_invoked(
    tmp_path: Path, bad: str
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"in_round_terminate = {bad}\n")

    with pytest.raises(ValueError, match="monitor.host_health.pressure.in_round_terminate"):
        load_config(toml)


@pytest.mark.parametrize("bad", ["0", "-1", '"x"', "true", "nan"])
def test_cgroup_growth_rate_warning_mb_per_min_should_reject_non_positive_else_bool_values_when_run(
    tmp_path: Path, bad: str
) -> None:

    toml = _write_toml(
        tmp_path, _HOST_HEALTH_PRESSURE_BASE + f"cgroup_growth_rate_warning_mb_per_min = {bad}\n"
    )

    with pytest.raises(
        ValueError, match="monitor.host_health.pressure.cgroup_growth_rate_warning_mb_per_min"
    ):
        load_config(toml)


@pytest.mark.parametrize(("literal", "expected"), [("256", 256.0), ("128.5", 128.5)])
def test_cgroup_growth_rate_warning_mb_per_min_should_accept_int_else_float_when_positive(
    tmp_path: Path, literal: str, expected: float
) -> None:

    toml = _write_toml(
        tmp_path,
        _HOST_HEALTH_PRESSURE_BASE + f"cgroup_growth_rate_warning_mb_per_min = {literal}\n",
    )

    cfg = load_config(toml)

    assert cfg.monitor.host_health.pressure.cgroup_growth_rate_warning_mb_per_min == expected


def test_cgroup_growth_rate_warning_mb_per_min_should_default_to_512_when_invoked() -> None:
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.pressure.cgroup_growth_rate_warning_mb_per_min == 512.0


_INJECT_CONTEXT_BASE = """\
[agent]
command = ["true"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "."
log_dir = "logs"
[prompt]
file = "p.md"
"""


def test_quoted_inject_context_should_raise_when_loaded(tmp_path: Path) -> None:
    """bool("false") is True — the operator gets the exact opposite of the request."""
    toml = _write_toml(tmp_path, _INJECT_CONTEXT_BASE + 'inject_context = "false"\n')

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert "prompt.inject_context" in str(exc.value)


def test_real_bool_inject_context_should_be_accepted_when_loaded(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _INJECT_CONTEXT_BASE + "inject_context = false\n")

    assert load_config(toml).prompt.inject_context is False


def test_invalid_auth_fail_pattern_should_raise_when_loaded(tmp_path: Path) -> None:
    """Mirrors runtime.grace_kill_ignore_patterns: an invalid regex must fail at
    load, not at first use inside the monitor."""
    toml = _write_toml(
        tmp_path,
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        '[prompt]\nfile="p.md"\n'
        '[monitor]\nauth_fail_patterns = ["[unclosed"]\n',
    )

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert "monitor.auth_fail_patterns" in str(exc.value)
    assert "invalid regex" in str(exc.value)


def test_removed_field_error_should_point_to_migrate_command_when_invoked(tmp_path):
    """Removed-field ConfigErrors must direct users to `agent-runner migrate`."""

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        "[agent]\n"
        'command = ["claude"]\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        'rate_limit_action = "back_off"\n\n'
        "[prompt]\n"
        f'file = "{tmp_path}/p.md"\n',
        encoding="utf-8",
    )
    (tmp_path / "p.md").write_text("x" * 800, encoding="utf-8")

    with pytest.raises(ConfigError, match="agent-runner migrate"):
        load_config(toml)
