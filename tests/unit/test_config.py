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
    assert cfg.phases.list is None
    assert cfg.phases.overrides == {}
    assert cfg.vcs.dirty_action == "stash"


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
    assert cfg.phases.list == ["diverge", "converge"]


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


def test_given_round_timeout_per_phase_dict_when_loaded_then_config_error_with_migration_hint(
    tmp_path: Path,
) -> None:
    """Old runtime.round_timeout_per_phase = {...} syntax → ConfigError with migration path."""
    import pytest

    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "round_timeout_per_phase = { dev = 3600, qa = 900 }\n"
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
        "[phases]\n"
        'list = ["dev", "qa"]\n'
    )

    with pytest.raises(
        ValueError,
        match=r"runtime\.round_timeout_per_phase.*removed in 0\.1\.16.*\[phases\.<name>\]",
    ):
        load_config(tmp_path / "agent-runner.toml")


def test_given_runtime_config_then_no_round_timeout_per_phase_field() -> None:
    """RuntimeConfig dataclass no longer has round_timeout_per_phase field."""
    import dataclasses

    from agent_runner.config import RuntimeConfig

    field_names = {f.name for f in dataclasses.fields(RuntimeConfig)}
    assert "round_timeout_per_phase" not in field_names


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


def test_given_phases_list_only_when_loaded_then_phases_config_with_empty_overrides(
    tmp_path: Path,
) -> None:
    """[phases].list set + no sub-tables → PhasesConfig with .list set and .overrides = {}."""
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
        "[phases]\n"
        'list = ["dev", "qa"]\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.phases.list == ["dev", "qa"]
    assert cfg.phases.overrides == {}


def test_given_phase_sub_table_round_timeout_when_loaded_then_override_recorded(
    tmp_path: Path,
) -> None:
    """[phases.dev] round_timeout_s=3600 → PhasesConfig.overrides['dev'].round_timeout_s == 3600."""
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
        "[phases]\n"
        'list = ["dev", "qa"]\n'
        "[phases.dev]\n"
        "round_timeout_s = 3600\n"
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.phases.overrides["dev"].round_timeout_s == 3600
    assert cfg.phases.overrides["dev"].disable_pre_round_hooks is None
    assert cfg.phases.overrides["dev"].prompt_files is None


def test_given_phase_sub_table_all_three_fields_when_loaded_then_all_parsed(
    tmp_path: Path,
) -> None:
    """All 3 whitelist fields under [phases.<name>] parse correctly."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
        "[phases]\n"
        'list = ["dev"]\n'
        "[phases.dev]\n"
        "round_timeout_s = 3600\n"
        "disable_pre_round_hooks = true\n"
        'prompt.files = ["a.md", "b.md"]\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    o = cfg.phases.overrides["dev"]
    assert o.round_timeout_s == 3600
    assert o.disable_pre_round_hooks is True
    assert o.prompt_files == [tmp_path / "a.md", tmp_path / "b.md"]


def test_given_phase_name_not_in_list_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """[phases.foo] where 'foo' not in phases.list → ConfigError."""
    import pytest

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
        "[phases]\n"
        'list = ["dev"]\n'
        "[phases.foo]\n"
        "round_timeout_s = 3600\n"
    )

    with pytest.raises(ValueError, match=r"\[phases\.foo\].*not in phases\.list"):
        load_config(tmp_path / "agent-runner.toml")


def test_given_unknown_field_in_phase_sub_table_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """[phases.dev] with unknown field → ConfigError listing allowed fields."""
    import pytest

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
        "[phases]\n"
        'list = ["dev"]\n'
        "[phases.dev]\n"
        "made_up_field = 42\n"
    )

    with pytest.raises(ValueError, match=r"unknown per-phase field.*made_up_field.*allowed"):
        load_config(tmp_path / "agent-runner.toml")


def test_given_no_phases_section_when_loaded_then_phases_config_none_list(
    tmp_path: Path,
) -> None:
    """Project without [phases] section → cfg.phases is PhasesConfig(list=None, overrides={})."""
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
    assert cfg.phases.list is None
    assert cfg.phases.overrides == {}


def test_given_prompt_files_list_when_loaded_then_files_attribute_set(
    tmp_path: Path,
) -> None:
    """[prompt] files = ["a.md", "b.md"] → cfg.prompt.files = [Path('a.md'), Path('b.md')]."""
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        'files = ["a.md", "b.md"]\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.prompt.files == [tmp_path / "a.md", tmp_path / "b.md"]
    assert cfg.prompt.file is None
    assert cfg.prompt.concat_separator == "\n\n"
    assert cfg.prompt.strip_yaml_frontmatter is True


def test_given_prompt_file_single_when_loaded_then_back_compat_path(
    tmp_path: Path,
) -> None:
    """[prompt] file = "x.md" still works (back-compat)."""
    from agent_runner.config import load_config

    (tmp_path / "x.md").write_text("x")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/x.md"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.prompt.file == tmp_path / "x.md"
    assert cfg.prompt.files == []


def test_given_both_prompt_file_and_files_set_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """Setting BOTH prompt.file and prompt.files → ConfigError."""
    import pytest

    from agent_runner.config import load_config

    (tmp_path / "x.md").write_text("x")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/x.md"\n'
        'files = ["other.md"]\n'
    )

    with pytest.raises(ValueError, match=r"prompt\.file.*prompt\.files.*not both"):
        load_config(tmp_path / "agent-runner.toml")


def test_given_custom_concat_separator_when_loaded_then_used(tmp_path: Path) -> None:
    """concat_separator override is honored."""
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("a")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        'files = ["a.md"]\n'
        'concat_separator = "\\n\\n---\\n\\n"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.prompt.concat_separator == "\n\n---\n\n"


def test_given_strip_yaml_frontmatter_false_when_loaded_then_opt_out_honored(
    tmp_path: Path,
) -> None:
    """strip_yaml_frontmatter = false opt-out is honored."""
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("a")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        'files = ["a.md"]\n'
        "strip_yaml_frontmatter = false\n"
    )

    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.prompt.strip_yaml_frontmatter is False


def test_given_dirty_action_stash_when_loaded_then_field_set(tmp_path: Path) -> None:
    """vcs.dirty_action = "stash" parses correctly."""
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
        "[vcs]\n"
        'dirty_action = "stash"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.vcs.dirty_action == "stash"


def test_given_dirty_action_ignore_when_loaded_then_field_set(tmp_path: Path) -> None:
    """vcs.dirty_action = "ignore" parses correctly."""
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
        "[vcs]\n"
        'dirty_action = "ignore"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.vcs.dirty_action == "ignore"


def test_given_dirty_action_auto_commit_when_loaded_then_field_set(tmp_path: Path) -> None:
    """vcs.dirty_action = "auto_commit" parses correctly."""
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
        "[vcs]\n"
        'dirty_action = "auto_commit"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.vcs.dirty_action == "auto_commit"


def test_given_dirty_action_invalid_value_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """vcs.dirty_action with invalid value → ConfigError listing allowed."""
    import pytest

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
        "[vcs]\n"
        'dirty_action = "explode"\n'
    )
    with pytest.raises(
        ValueError, match=r"vcs\.dirty_action.*explode.*allowed.*stash.*ignore.*auto_commit"
    ):
        load_config(tmp_path / "agent-runner.toml")


def test_given_orphan_action_in_toml_when_loaded_then_raises_with_migration_hint(
    tmp_path: Path,
) -> None:
    """vcs.orphan_action removed in 0.1.18 — TOML using it must raise with migration hint."""
    cfg_path = tmp_path / "agent-runner.toml"
    (tmp_path / "p.md").write_text("hi")
    cfg_path.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/p.md"\n'
        "[vcs]\n"
        'orphan_action = "stash"\n'
    )
    with pytest.raises(ValueError, match=r"vcs\.orphan_action removed in 0\.1\.18"):
        load_config(cfg_path)


def test_given_relative_log_dir_when_loaded_then_resolved_to_absolute(
    tmp_path: Path,
) -> None:
    """log_dir = "logs" (relative) → resolved against work_dir at load."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        'log_dir = "logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.runtime.log_dir.is_absolute()
    assert cfg.runtime.log_dir == (tmp_path / "logs").resolve()


def test_given_relative_narrative_file_when_loaded_then_resolved(tmp_path: Path) -> None:
    """narrative_file = "narrative.md" → absolute after load."""
    from agent_runner.config import load_config

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        'narrative_file = "narrative.md"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.runtime.narrative_file is not None
    assert cfg.runtime.narrative_file.is_absolute()
    assert cfg.runtime.narrative_file == (tmp_path / "narrative.md").resolve()


def test_given_relative_prompt_file_when_loaded_then_resolved(tmp_path: Path) -> None:
    """prompt.file = "prompt.md" (relative) → absolute after load."""
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
        'file = "prompt.md"\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    assert cfg.prompt.file is not None
    assert cfg.prompt.file.is_absolute()
    assert cfg.prompt.file == (tmp_path / "prompt.md").resolve()


def test_given_relative_prompt_files_list_when_loaded_then_all_resolved(
    tmp_path: Path,
) -> None:
    """prompt.files = ["a.md", "b.md"] (both relative) → both absolute."""
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        'files = ["a.md", "b.md"]\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    for path in cfg.prompt.files:
        assert path.is_absolute()
    assert cfg.prompt.files[0] == (tmp_path / "a.md").resolve()
    assert cfg.prompt.files[1] == (tmp_path / "b.md").resolve()


def test_given_relative_per_phase_prompt_files_when_loaded_then_resolved(
    tmp_path: Path,
) -> None:
    """[phases.qa] prompt.files = ["x.md"] (relative) → absolute after load."""
    from agent_runner.config import load_config

    (tmp_path / "p.md").write_text("p")
    (tmp_path / "x.md").write_text("x")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        'file = "p.md"\n'
        "[phases]\n"
        'list = ["dev", "qa"]\n'
        "[phases.qa]\n"
        'prompt.files = ["x.md"]\n'
    )
    cfg = load_config(tmp_path / "agent-runner.toml")
    qa = cfg.phases.overrides["qa"]
    assert qa.prompt_files is not None
    assert all(p.is_absolute() for p in qa.prompt_files)
    assert qa.prompt_files[0] == (tmp_path / "x.md").resolve()


def test_given_no_narrative_file_when_loaded_then_remains_none(tmp_path: Path) -> None:
    """narrative_file unset → cfg.runtime.narrative_file is None (no resolution attempt)."""
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
    assert cfg.runtime.narrative_file is None


def test_rate_limit_action_in_toml_raises_config_error_with_migration_hint(tmp_path):
    """0.1.29: alias removed. TOML containing rate_limit_action must error."""
    from agent_runner.config import ConfigError, load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["claude"]\n'
        'name = "claude"\n'
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
    with pytest.raises(ConfigError) as exc_info:
        load_config(toml)
    assert "rate_limit_action" in str(exc_info.value)
    assert "transient_error_action" in str(exc_info.value)
    assert "0.1.29" in str(exc_info.value)


def test_transient_error_action_still_accepted(tmp_path):
    """Sanity: canonical key still works post-alias-removal."""
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["claude"]\n'
        'name = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        'transient_error_action = "stop"\n\n'
        "[prompt]\n"
        f'file = "{tmp_path}/p.md"\n',
        encoding="utf-8",
    )
    (tmp_path / "p.md").write_text("x" * 800, encoding="utf-8")
    cfg = load_config(toml)
    assert cfg.runtime.transient_error_action == "stop"


def test_runtime_dry_run_loads_from_toml(tmp_path):
    """[runtime] dry_run = true populates cfg.runtime.dry_run."""
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml.write_text(
        "[agent]\n"
        'command = ["claude"]\n'
        'name = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "dry_run = true\n\n"
        "[prompt]\n"
        f'file = "{prompt_file}"\n',
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.runtime.dry_run is True


def test_runtime_dry_run_default_false(tmp_path):
    """[runtime] dry_run omitted -> cfg.runtime.dry_run defaults to False."""
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml.write_text(
        "[agent]\n"
        'command = ["claude"]\n'
        'name = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n',
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.runtime.dry_run is False


def test_given_monitor_host_health_defaults_when_inspected_then_match_detector_defaults(
    tmp_path: Path,
) -> None:
    """Regression: MonitorHostHealthConfig defaults must match detector hardcoded thresholds."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()
    assert cfg.mem_avail_min_mb == 200
    assert cfg.disk_warning_pct == 90.0
    assert cfg.disk_critical_pct == 95.0


def test_given_monitor_host_health_toml_section_when_loaded_then_overrides_applied(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        '[agent]\ncommand = ["claude"]\nname = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        f'[runtime]\nwork_dir = "."\nlog_dir = "{tmp_path}/logs"\n\n'
        f'[prompt]\nfile = "{prompt_file}"\n\n'
        "[monitor.host_health]\nmem_avail_min_mb = 1000\ndisk_warning_pct = 85.0\n",
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.monitor.host_health.mem_avail_min_mb == 1000
    assert cfg.monitor.host_health.disk_warning_pct == 85.0
    assert cfg.monitor.host_health.disk_critical_pct == 95.0  # still default


def test_given_custom_mem_threshold_in_config_when_detect_mem_pressure_then_uses_config(
    tmp_path: Path,
) -> None:
    """Detector fires at config-defined threshold, not hardcoded default."""
    from agent_runner.config import MonitorConfig, MonitorHostHealthConfig
    from agent_runner.monitor import detect_mem_pressure

    host_health = MonitorHostHealthConfig(mem_avail_min_mb=500)
    monitor_cfg = MonitorConfig(host_health=host_health)

    # Value=300 is below custom threshold (500) but above default (200)
    metrics = [{"mem_available_mb": 300}]
    alert = detect_mem_pressure(metrics, threshold_mb=monitor_cfg.host_health.mem_avail_min_mb)
    assert alert is not None
    assert alert.detector == "mem_pressure"


def test_given_host_health_overrides_when_run_all_detectors_then_thresholds_applied(
    tmp_path: Path,
) -> None:
    """Regression: run_all_detectors must plumb host_health thresholds to detectors.

    Pre-fix, the config was defined but never passed into run_all_detectors, so the
    TOML override silently no-op'd in production. This test exercises the wired path.
    """
    from agent_runner.monitor import run_all_detectors

    # mem_available_mb=300: below custom mem_avail_min_mb=500 but above default 200
    metrics = [{"mem_available_mb": 300, "disk_used_pct": 92.0}]

    alerts = run_all_detectors(
        events=[],
        metrics=metrics,
        log_tails={},
        mem_avail_min_mb=500,
        disk_warning_pct=85.0,
        disk_critical_pct=95.0,
    )
    kinds = {a.detector for a in alerts}
    assert "mem_pressure" in kinds  # 300 < 500
    assert "disk_warning" in kinds  # 92 in [85, 95)

    # With defaults, neither would fire at these values
    alerts_default = run_all_detectors(events=[], metrics=metrics, log_tails={})
    kinds_default = {a.detector for a in alerts_default}
    assert "mem_pressure" not in kinds_default  # 300 > default 200
    assert "disk_warning" in kinds_default  # 92 still > default 90


def test_given_high_disk_critical_when_disk_used_below_then_warning_still_fires(
    tmp_path: Path,
) -> None:
    """detect_disk_warning's upper bound must scale with disk_critical_pct.

    Pre-fix: hardcoded `val >= 95.0` masked warnings at 96–98% when critical was 98%.
    """
    from agent_runner.monitor import detect_disk_warning

    metrics = [{"disk_used_pct": 96.0}]
    # critical_pct=98 → 96 should fire as warning
    alert = detect_disk_warning(metrics, threshold_pct=90.0, critical_pct=98.0)
    assert alert is not None
    assert alert.detector == "disk_warning"


def test_given_no_supervisor_stale_field_then_default_none(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        '[runtime]\nwork_dir = "."\nlog_dir = "/tmp/logs"\n'
        '[prompt]\nfile = "p.md"\n',
    )
    cfg = load_config(toml)
    assert cfg.monitor.supervisor_stale_threshold_s is None


def test_given_supervisor_stale_threshold_set_then_loaded(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        '[runtime]\nwork_dir = "."\nlog_dir = "/tmp/logs"\n'
        '[prompt]\nfile = "p.md"\n'
        "[monitor]\nsupervisor_stale_threshold_s = 600\n",
    )
    cfg = load_config(toml)
    assert cfg.monitor.supervisor_stale_threshold_s == 600
