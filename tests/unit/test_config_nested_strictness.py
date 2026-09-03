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
    p.write_text(body, encoding="utf-8")
    return p


def test_agent_env_as_scalar_raises_configerror(tmp_path: Path) -> None:
    # Reached mid-[agent]-parse, before any other table is even consulted, so
    # only [agent] itself needs to be otherwise-valid.
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\nenv = "oops"\n',
    )
    with pytest.raises(ConfigError, match=r"\[agent\.env\]"):
        load_config(p)


def test_monitor_host_health_as_scalar_raises_configerror(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor]\nhost_health = 1\n",
    )
    with pytest.raises(ConfigError, match=r"\[monitor\.host_health\]"):
        load_config(p)


def test_monitor_host_health_unknown_key_raises_configerror(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health]\nbogus = 1\n",
    )
    with pytest.raises(ConfigError, match=r"\[monitor\.host_health\]"):
        load_config(p)


def test_per_phase_schedule_bad_key_names_the_phase(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\n'
        '[phases]\nlist = ["dev"]\n'
        "[phases.dev.schedule]\nbogus = 1\n",
    )
    with pytest.raises(ConfigError, match=r"phases\.dev\.schedule"):
        load_config(p)


def test_top_level_schedule_bad_key_still_names_schedule(tmp_path: Path) -> None:
    """Regression guard for the label default: a top-level [schedule] bad key
    must still report `[schedule]`, not some leaked per-phase label."""
    p = _write(
        tmp_path,
        '[agent]\ncommand = ["x"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[schedule]\nbogus = 1\n",
    )
    with pytest.raises(ConfigError, match=r"unknown \[schedule\]"):
        load_config(p)
