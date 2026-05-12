from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text(body)
    return p


def test_given_minimal_toml_when_loaded_then_returns_config_with_defaults(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["claude", "--model", "claude-opus-4-7"]
prompt_arg_template = ["-p", "{prompt}"]

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[prompt]
file = "./prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.command == ["claude", "--model", "claude-opus-4-7"]
    assert cfg.runtime.round_timeout_s == 1800  # default
    assert cfg.runtime.restart_delay_s == 3
    assert cfg.prompt.inject_context is True  # default
    assert cfg.phases is None
    assert cfg.vcs.orphan_action == "stash"


def test_given_phases_in_toml_when_loaded_then_phases_list_populated(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["claude"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "./p.md"
[phases]
list = ["diverge", "converge"]
""",
    )
    cfg = load_config(toml)
    assert cfg.phases == ["diverge", "converge"]


def test_given_missing_required_field_when_loaded_then_raises_with_field_name(
    tmp_path: Path,
) -> None:
    toml = _write_toml(tmp_path, "[agent]\ncommand = []\n")
    with pytest.raises(ValueError, match="agent.prompt_arg_template"):
        load_config(toml)


def test_given_log_dir_with_project_placeholder_when_loaded_then_substituted(
    tmp_path: Path,
) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["claude"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "/home/me/myproj"
log_dir = "~/.agent-runner/{project}/logs"
[prompt]
file = "./p.md"
""",
    )
    cfg = load_config(toml)
    assert "/myproj/logs" in str(cfg.runtime.log_dir)


def test_given_nonexistent_toml_when_loaded_then_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_given_work_dir_dot_when_loaded_then_project_resolves_to_cwd_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """work_dir='.' should resolve to actual cwd, not literal 'default'."""
    monkeypatch.chdir(tmp_path)
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["claude"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/{project}/logs"
[prompt]
file = "./p.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.runtime.work_dir.name == tmp_path.name
    assert tmp_path.name in str(cfg.runtime.log_dir)


def test_given_agent_name_in_toml_when_loaded_then_name_set(tmp_path):
    cfg_path = tmp_path / "agent-runner.toml"
    cfg_path.write_text(
        '[agent]\n'
        'name = "claude"\n'
        'command = ["claude"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        '[runtime]\n'
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        '[prompt]\n'
        'file = "prompts/main.md"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.agent.name == "claude"


def test_given_agent_without_name_when_loaded_then_name_is_none(tmp_path):
    cfg_path = tmp_path / "agent-runner.toml"
    cfg_path.write_text(
        '[agent]\n'
        'command = ["claude"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        '[runtime]\n'
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        '[prompt]\n'
        'file = "prompts/main.md"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.agent.name is None
