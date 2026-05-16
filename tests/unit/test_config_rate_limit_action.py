"""Unit tests for runtime.rate_limit_action config field."""

from __future__ import annotations

from pathlib import Path

import pytest


def _minimal_toml(work_dir: Path, runtime_extra: str = "") -> Path:
    """Write a minimal valid TOML with optional runtime additions."""
    (work_dir / "p.md").write_text("hi")
    cfg_path = work_dir / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\n"
        'command = ["echo"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{work_dir}/logs"\n'
        f"{runtime_extra}"
        "[prompt]\n"
        f'file = "{work_dir}/p.md"\n'
    )
    return cfg_path


@pytest.mark.parametrize("value", ["back_off", "skip", "stop"])
def test_given_valid_rate_limit_action_when_loaded_then_parses(tmp_path, value):
    from agent_runner.config import load_config

    cfg_path = _minimal_toml(tmp_path, runtime_extra=f'rate_limit_action = "{value}"\n')
    cfg = load_config(cfg_path)
    assert cfg.runtime.rate_limit_action == value


def test_given_no_rate_limit_action_when_loaded_then_defaults_to_back_off(tmp_path):
    from agent_runner.config import load_config

    cfg_path = _minimal_toml(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.rate_limit_action == "back_off"


def test_given_invalid_rate_limit_action_when_loaded_then_raises(tmp_path):
    from agent_runner.config import load_config

    cfg_path = _minimal_toml(tmp_path, runtime_extra='rate_limit_action = "explode"\n')
    with pytest.raises(
        ValueError, match=r"runtime\.rate_limit_action.*explode.*back_off.*skip.*stop"
    ):
        load_config(cfg_path)
