from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text(body)
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


def test_given_minimal_toml_when_loaded_then_returns_config_with_defaults(
    tmp_path: Path,
) -> None:
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


def test_given_phases_in_toml_when_loaded_then_phases_list_populated(
    tmp_path: Path,
) -> None:
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


def test_given_nonexistent_toml_when_loaded_then_raises_filenotfound(
    tmp_path: Path,
) -> None:
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


def test_given_injection_mode_explicit_when_loaded_then_mode_set(
    tmp_path: Path,
) -> None:
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


def test_given_injection_mode_absent_when_loaded_then_default_is_prepend(
    tmp_path: Path,
) -> None:
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


def test_given_no_monitor_block_when_loaded_then_default_patterns(
    tmp_path: Path,
) -> None:
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


def test_given_no_plugins_block_when_loaded_then_plugins_empty_defaults(
    tmp_path: Path,
) -> None:
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
    assert cfg.plugins.disable == [] and cfg.plugins.raw == {}


def test_given_plugins_block_present_when_loaded_then_passes_through(
    tmp_path: Path,
) -> None:
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
    assert cfg.plugins.raw == {"disabled": ["future_plugin_name"]}


def test_given_no_auto_stop_on_when_loaded_then_default_includes_builtins(
    tmp_path: Path,
) -> None:
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
    assert cfg.monitor.auto_stop_on == [
        "oauth_fail",
        "disk_critical",
        "my_plugin_critical",
    ]


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


def test_given_no_agent_env_block_when_loaded_then_env_is_empty_dict(
    tmp_path: Path,
) -> None:
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


def test_given_agent_env_non_string_values_when_loaded_then_coerced_to_str(
    tmp_path: Path,
) -> None:
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


def test_given_per_phase_timeouts_when_loaded_then_dict_populated(
    tmp_path: Path,
) -> None:
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


def test_given_round_timeout_s_bool_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """Apply same type-guard to runtime.round_timeout_s (was silently coercing)."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
round_timeout_s = true
[prompt]
file = "prompts/main.md"
""",
    )
    with pytest.raises(ValueError, match="round_timeout_s.*must be an integer"):
        load_config(toml)


def test_given_restart_delay_s_zero_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """Apply positive-int check to runtime.restart_delay_s."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
restart_delay_s = 0
[prompt]
file = "prompts/main.md"
""",
    )
    with pytest.raises(ValueError, match="restart_delay_s.*must be positive"):
        load_config(toml)


def test_given_stash_idempotency_s_float_when_loaded_then_raises_value_error(
    tmp_path: Path,
) -> None:
    """Apply type-guard to vcs.stash_idempotency_s."""
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
[vcs]
stash_idempotency_s = 1.5
""",
    )
    with pytest.raises(ValueError, match="stash_idempotency_s.*must be an integer"):
        load_config(toml)


def test_given_no_monitor_block_when_load_config_then_remote_failure_tolerance_defaults_90(
    tmp_path: Path,
) -> None:
    """Default value when [monitor] block absent."""
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
    assert cfg.monitor.remote_failure_tolerance_s == 90


def test_given_custom_tolerance_when_load_config_then_parsed(tmp_path: Path) -> None:
    """[monitor] remote_failure_tolerance_s = N is honored."""
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
remote_failure_tolerance_s = 120
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.remote_failure_tolerance_s == 120


def test_given_zero_tolerance_when_load_config_then_accepted_as_opt_out(
    tmp_path: Path,
) -> None:
    """0 is valid (opt-out of retry, 0.1.10 immediate-propagate behavior)."""
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
remote_failure_tolerance_s = 0
""",
    )
    cfg = load_config(toml)
    assert cfg.monitor.remote_failure_tolerance_s == 0


def test_given_negative_tolerance_when_load_config_then_raises(tmp_path: Path) -> None:
    """Negative values are rejected by _require_non_negative_int."""
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
remote_failure_tolerance_s = -5
""",
    )
    with pytest.raises(ValueError, match="must be >= 0"):
        load_config(toml)


def test_given_excessive_tolerance_when_load_config_then_raises(tmp_path: Path) -> None:
    """remote_failure_tolerance_s must be <= 3600 (one-hour sanity cap)."""
    body = (
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
        "[monitor]\n"
        "remote_failure_tolerance_s = 86400\n"
    )
    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(tmp_path, body)
    with pytest.raises(ValueError, match="must be <= 3600"):
        load_config(cfg_path)


def test_given_no_plugins_block_when_load_config_then_plugins_defaults_empty(
    tmp_path: Path,
) -> None:
    """Default PluginsConfig: disable=[] and raw={}."""
    from agent_runner.config import PluginsConfig, load_config

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(tmp_path, _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path))
    cfg = load_config(cfg_path)
    assert isinstance(cfg.plugins, PluginsConfig)
    assert cfg.plugins.disable == []
    assert cfg.plugins.raw == {}


def test_given_plugins_disable_list_when_load_config_then_parsed(
    tmp_path: Path,
) -> None:
    """[plugins] disable = [...] is parsed into PluginsConfig.disable.

    The names don't match any registered plugin in test env, so apply_plugin_disable
    emits a UserWarning — assert it explicitly.
    """
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path)
        + '\n[plugins]\ndisable = ["argus_prompt_assembly", "argus_chain_state"]\n',
    )
    with pytest.warns(UserWarning, match="argus_prompt_assembly"):
        cfg = load_config(cfg_path)
    assert cfg.plugins.disable == ["argus_prompt_assembly", "argus_chain_state"]
    assert cfg.plugins.raw == {}


def test_given_plugins_unknown_keys_when_load_config_then_preserved_in_raw(
    tmp_path: Path,
) -> None:
    """Plugin-author-defined keys land in PluginsConfig.raw (forward-compat)."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path) + '\n[plugins]\nargus_foo = "bar"\n',
    )
    cfg = load_config(cfg_path)
    assert cfg.plugins.disable == []
    assert cfg.plugins.raw == {"argus_foo": "bar"}


def test_given_no_disable_hooks_when_load_config_then_defaults_false(
    tmp_path: Path,
) -> None:
    """RuntimeConfig.disable_pre_round_hooks defaults to False."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(tmp_path, _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path))
    cfg = load_config(cfg_path)
    assert cfg.runtime.disable_pre_round_hooks is False


def test_given_disable_hooks_true_when_load_config_then_honored(
    tmp_path: Path,
) -> None:
    """[runtime] disable_pre_round_hooks = true is parsed."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    body = _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path).replace(
        "[runtime]",
        "[runtime]\ndisable_pre_round_hooks = true",
    )
    cfg_path = _write_toml(tmp_path, body)
    cfg = load_config(cfg_path)
    assert cfg.runtime.disable_pre_round_hooks is True


def test_given_disable_hooks_non_bool_when_load_config_then_raises(
    tmp_path: Path,
) -> None:
    """Non-bool value rejected at parse time."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    body = _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path).replace(
        "[runtime]",
        "[runtime]\ndisable_pre_round_hooks = 'yes'",
    )
    cfg_path = _write_toml(tmp_path, body)
    with pytest.raises(ValueError, match="must be a bool"):
        load_config(cfg_path)


def test_given_default_runtime_when_loaded_then_round_log_retention_is_100(
    tmp_path: Path,
) -> None:
    """RuntimeConfig.round_log_retention defaults to 100 when unset in TOML."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.runtime.round_log_retention == 100
    assert cfg.runtime.narrative_file is None


def test_given_explicit_round_log_retention_when_loaded_then_used(
    tmp_path: Path,
) -> None:
    """round_log_retention=50 overrides default."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "round_log_retention = 50\n"
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.runtime.round_log_retention == 50


def test_given_explicit_narrative_file_when_loaded_then_resolved_path(
    tmp_path: Path,
) -> None:
    """narrative_file points to a custom location, resolved as Path."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        f'narrative_file = "{tmp_path}/notes.md"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.runtime.narrative_file == tmp_path / "notes.md"
