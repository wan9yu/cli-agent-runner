"""0.2.14 nested-table strictness completion (BREAKING via migrate).

The 0.2.13 sweep rejected unknown/scalar TOP-LEVEL tables but missed two nested
ones: ``[agent] env = "x"`` and ``[monitor] host_health = 1`` raised a raw
``AttributeError`` (never classified permanent-78) instead of ``ConfigError``,
and ``[monitor.host_health] bogus = 1`` loaded silently — an operator's
typo'd threshold dropped with no signal. This file pins the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import ConfigError, load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text("schema_version = 1\n" + body, encoding="utf-8")
    return p


def test_load_config_should_raise_configerror_when_agent_env_is_scalar(tmp_path: Path) -> None:
    # Reached mid-[agent]-parse, before any other table is even consulted, so
    # only [agent] itself needs to be otherwise-valid.

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\nenv = "oops"\n',
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "[agent.env]" in actual


def test_load_config_should_raise_configerror_when_monitor_host_health_is_scalar(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor]\nhost_health = 1\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "[monitor.host_health]" in actual


def test_load_config_should_raise_configerror_when_monitor_host_health_has_unknown_key(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health]\nbogus = 1\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "[monitor.host_health]" in actual


def test_load_config_should_raise_configerror_when_brake_has_unknown_key(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health.brake]\nbogus = 1\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "monitor.host_health.brake" in actual


def test_load_config_should_raise_configerror_when_brake_step_pct_over_cap(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health.brake]\nmemory_high_step_pct = 51\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "must be <= 50" in actual


def test_load_config_should_accept_brake_step_pct_zero_for_cap_at_current_when_invoked(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health.brake]\nmemory_high_step_pct = 0\n",
    )

    cfg = load_config(p)

    assert cfg.monitor.host_health.brake.memory_high_step_pct == 0


def test_load_config_should_raise_configerror_when_brake_step_pct_negative(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health.brake]\nmemory_high_step_pct = -1\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "must be >= 0" in actual


def test_load_config_should_name_the_phase_when_per_phase_schedule_has_bad_key(
    tmp_path: Path,
) -> None:

    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\n'
        '[phases]\nlist = ["dev"]\n'
        "[phases.dev.schedule]\nbogus = 1\n",
    )

    with pytest.raises(ConfigError) as caught:
        load_config(p)

    actual = str(caught.value)
    assert "phases.dev.schedule" in actual
