"""Control-char fail-closed hardening (0.2.13 Group D).

A newline (or other non-printable char) in ``work_dir``/``log_dir``/the config
path itself must never reach a rendered systemd unit, where it would inject
an arbitrary extra directive (e.g. ``User=root``) via ``install --system``.
Checked both at config load (fail-closed ``ConfigError``) and again at render
time (defense in depth — a ``Config`` can be built directly, bypassing
``load_config`` entirely, as ``test_service_unit.py`` already does)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import (
    AgentConfig,
    Config,
    ConfigError,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
    load_config,
)
from agent_runner.service_unit import render_monitor_unit, render_serve_unit


def _base(wd: Path, *, work_dir_value: str | None = None, log_dir_value: str | None = None) -> str:
    wd_str = work_dir_value if work_dir_value is not None else str(wd)
    log_dir_str = log_dir_value if log_dir_value is not None else f"{wd}/logs"
    return (
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["-p"]\n'
        "[runtime]\n"
        f'work_dir = "{wd_str}"\n'
        f'log_dir = "{log_dir_str}"\n'
        "[prompt]\n"
        f'file = "{wd}/prompt.md"\n'
    )


def _write(tmp_path: Path, body: str, name: str = "agent-runner.toml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# Literal TOML basic-string escapes (decoded by tomllib into a real control
# char, exactly like a crafted agent-runner.toml would) -- \n, \r, \t are the
# 3 spec-review-flagged repro cases; a NUL byte is the one that previously
# slipped past the check entirely (Path.resolve() raises a bare ValueError on
# a NUL byte BEFORE the control-char check ever ran, for both an absolute
# work_dir -- always resolved -- and a RELATIVE log_dir -- resolved only on
# that branch).
_CONTROL_ESCAPES = {
    "newline": "\\n",
    "carriage-return": "\\r",
    "tab": "\\t",
    "nul": "\\u0000",
}


@pytest.mark.parametrize("escape", _CONTROL_ESCAPES.values(), ids=_CONTROL_ESCAPES.keys())
def test_given_control_char_in_work_dir_when_load_config_then_config_error(
    tmp_path: Path, escape: str
) -> None:
    injected = f"{tmp_path}{escape}User=root"
    p = _write(tmp_path, _base(tmp_path, work_dir_value=injected))
    with pytest.raises(ConfigError, match="work_dir"):
        load_config(p)


@pytest.mark.parametrize("escape", _CONTROL_ESCAPES.values(), ids=_CONTROL_ESCAPES.keys())
def test_given_control_char_in_relative_log_dir_when_load_config_then_config_error(
    tmp_path: Path, escape: str
) -> None:
    """A RELATIVE log_dir is the one that actually exercises Path.resolve()
    (_resolve_against_work_dir short-circuits an absolute path without ever
    calling it) -- this is the exact shape the NUL-byte ordering bug needs to
    reproduce."""
    injected = f"logs{escape}User=root"
    p = _write(tmp_path, _base(tmp_path, log_dir_value=injected))
    with pytest.raises(ConfigError, match="log_dir"):
        load_config(p)


def test_given_newline_in_config_path_when_load_config_then_config_error(tmp_path: Path) -> None:
    weird = _write(tmp_path, _base(tmp_path), name="agent-runner.toml\nUser=root")
    with pytest.raises(ConfigError, match="config path"):
        load_config(weird)


def _direct_cfg(work_dir: Path, tmp_path: Path) -> Config:
    """A Config built directly, the way ``test_service_unit.py`` does --
    never passes through ``load_config``'s own guard."""
    return Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=work_dir, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=tmp_path / "p.md"),
        vcs=VcsConfig(),
    )


def test_given_newline_in_work_dir_when_render_serve_unit_then_config_error(
    tmp_path: Path,
) -> None:
    poisoned_work_dir = Path(f"{tmp_path}\nUser=root")
    cfg = _direct_cfg(poisoned_work_dir, tmp_path)
    with pytest.raises(ConfigError, match="work_dir"):
        render_serve_unit(cfg, script_path=tmp_path / "ar", config_path=tmp_path / "a.toml")


def test_given_newline_in_config_path_when_render_serve_unit_then_config_error(
    tmp_path: Path,
) -> None:
    cfg = _direct_cfg(tmp_path, tmp_path)
    poisoned_config_path = Path(f"{tmp_path}/a.toml\nUser=root")
    with pytest.raises(ConfigError, match="config path"):
        render_serve_unit(cfg, script_path=tmp_path / "ar", config_path=poisoned_config_path)


def test_given_newline_in_config_path_when_render_monitor_unit_then_config_error(
    tmp_path: Path,
) -> None:
    cfg = _direct_cfg(tmp_path, tmp_path)
    poisoned_config_path = Path(f"{tmp_path}/a.toml\nUser=root")
    with pytest.raises(ConfigError, match="config path"):
        render_monitor_unit(cfg, script_path=tmp_path / "ar", config_path=poisoned_config_path)
