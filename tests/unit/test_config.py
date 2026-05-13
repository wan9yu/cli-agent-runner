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
command = ["my-agent", "--model", "x"]
prompt_arg_template = ["-p", "{prompt}"]

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[prompt]
file = "./prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.command == ["my-agent", "--model", "x"]
    assert cfg.runtime.round_timeout_s == 1800  # default
    assert cfg.runtime.restart_delay_s == 3
    assert cfg.prompt.inject_context is True  # default
    assert cfg.phases is None
    assert cfg.vcs.orphan_action == "stash"
    assert cfg.runtime.round_timeout_per_phase == {}


def test_given_phases_in_toml_when_loaded_then_phases_list_populated(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
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
    with pytest.raises(ValueError, match="prompt_arg_template"):
        load_config(toml)


def test_given_log_dir_with_project_placeholder_when_loaded_then_substituted(
    tmp_path: Path,
) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
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
command = ["my-agent"]
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


def test_given_agent_name_in_toml_when_loaded_then_name_set(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
name = "claude"
command = ["claude"]
prompt_arg_template = ["{prompt}"]

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.name == "claude"


def test_given_agent_without_name_when_loaded_then_name_is_none(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.name is None


def test_given_injection_mode_explicit_when_loaded_then_mode_set(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
context_injection_mode = "file"
""",
    )
    cfg = load_config(toml)
    assert cfg.prompt.context_injection_mode == "file"


def test_given_injection_mode_absent_when_loaded_then_default_is_prepend(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.prompt.context_injection_mode == "prepend"


def test_given_invalid_injection_mode_when_loaded_then_raises(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
context_injection_mode = "magic"
""",
    )
    with pytest.raises(ValueError, match="context_injection_mode"):
        load_config(toml)


def test_given_no_monitor_block_when_loaded_then_default_patterns(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert isinstance(cfg.monitor.auth_fail_patterns, list)
    assert len(cfg.monitor.auth_fail_patterns) >= 1
    assert isinstance(cfg.monitor.auth_fail_hint, str)


def test_given_custom_auth_patterns_when_loaded_then_used(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
[monitor]
auth_fail_patterns = ["custom_oauth_regex", "another_pattern"]
auth_fail_hint = "Custom hint for non-claude provider"
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.auth_fail_patterns == ["custom_oauth_regex", "another_pattern"]
    assert cfg.monitor.auth_fail_hint == "Custom hint for non-claude provider"


def test_given_no_plugins_block_when_loaded_then_plugins_is_none(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.plugins is None


def test_given_plugins_block_present_when_loaded_then_passes_through(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
[plugins]
disabled = ["future_plugin_name"]
""",
    )
    cfg = load_config(toml)
    assert cfg.plugins == {"disabled": ["future_plugin_name"]}


def test_given_no_auto_stop_on_when_loaded_then_default_includes_builtins(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.auto_stop_on == ["oauth_fail", "disk_critical"]


def test_given_custom_auto_stop_on_when_loaded_then_used(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
[monitor]
auto_stop_on = ["oauth_fail", "disk_critical", "my_plugin_critical"]
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.auto_stop_on == ["oauth_fail", "disk_critical", "my_plugin_critical"]


def test_given_agent_env_block_when_loaded_then_env_populated(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]

[agent.env]
DISABLE_AUTOUPDATER = "1"
SOME_FLAG = "yes"

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.env == {"DISABLE_AUTOUPDATER": "1", "SOME_FLAG": "yes"}


def test_given_no_agent_env_block_when_loaded_then_env_is_empty_dict(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.env == {}


def test_given_agent_env_non_string_values_when_loaded_then_coerced_to_str(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[agent.env]
INT_FLAG = 42
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.agent.env == {"INT_FLAG": "42"}


def test_given_no_auth_fail_hint_in_toml_when_loaded_then_default_is_empty_string(
    tmp_path: Path,
) -> None:
    """0.1.7: default hint moves to preset files; bare config gets empty default."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.auth_fail_hint == ""


def test_given_per_phase_timeouts_when_loaded_then_dict_populated(tmp_path: Path) -> None:
    """0.1.9: [runtime.round_timeout_per_phase] parsed to dict[str, int]."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]

[runtime]
work_dir = "."
log_dir = "/tmp/logs"

[runtime.round_timeout_per_phase]
dev = 3600
qa = 1200
product = 1200

[prompt]
file = "prompts/main.md"

[phases]
list = ["dev", "qa", "product"]
""",
    )
    cfg = load_config(toml)
    assert cfg.runtime.round_timeout_per_phase == {
        "dev": 3600,
        "qa": 1200,
        "product": 1200,
    }


def test_given_no_per_phase_block_when_loaded_then_empty_dict(tmp_path: Path) -> None:
    """0.1.9: absent block -> empty dict default; zero behavior change."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[prompt]
file = "prompts/main.md"
""",
    )
    cfg = load_config(toml)
    assert cfg.runtime.round_timeout_per_phase == {}


def test_given_per_phase_typo_key_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """0.1.9: key not in phases.list -> ValueError naming the offender."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[runtime.round_timeout_per_phase]
foo = 600
[prompt]
file = "prompts/main.md"
[phases]
list = ["dev", "qa"]
""",
    )
    with pytest.raises(ValueError, match="foo"):
        load_config(toml)


def test_given_per_phase_non_positive_value_when_loaded_then_raises(
    tmp_path: Path,
) -> None:
    """0.1.9: zero or negative timeout -> ValueError."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[runtime.round_timeout_per_phase]
dev = 0
[prompt]
file = "prompts/main.md"
[phases]
list = ["dev"]
""",
    )
    with pytest.raises(ValueError, match="positive"):
        load_config(toml)


def test_given_per_phase_without_phases_list_when_loaded_then_raises(
    tmp_path: Path,
) -> None:
    """0.1.9: non-empty per_phase + missing [phases] list -> ValueError."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[runtime.round_timeout_per_phase]
dev = 1800
[prompt]
file = "prompts/main.md"
""",
    )
    with pytest.raises(ValueError, match="phases"):
        load_config(toml)


def test_given_per_phase_bool_value_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """0.1.9: bool values rejected (bool is a subclass of int in Python but
    'dev = true' as a timeout is almost certainly a typo)."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[runtime.round_timeout_per_phase]
dev = true
[prompt]
file = "prompts/main.md"
[phases]
list = ["dev"]
""",
    )
    with pytest.raises(ValueError, match="must be an integer"):
        load_config(toml)


def test_given_per_phase_float_value_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """0.1.9: float values rejected to prevent silent truncation."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
[runtime.round_timeout_per_phase]
dev = 1.5
[prompt]
file = "prompts/main.md"
[phases]
list = ["dev"]
""",
    )
    with pytest.raises(ValueError, match="must be an integer"):
        load_config(toml)
