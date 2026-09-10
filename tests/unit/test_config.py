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


def test_minimal_toml_should_return_config_with_defaults_when_loaded(
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


def test_phases_in_toml_should_populate_phases_list_when_loaded(
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


def test_missing_required_field_should_raise_with_field_name_when_loaded(
    tmp_path: Path,
) -> None:
    toml = _write_toml(tmp_path, "[agent]\ncommand = []\n")

    with pytest.raises(ValueError, match="prompt_arg_template"):
        load_config(toml)


def test_log_dir_with_project_placeholder_should_be_substituted_when_loaded(
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


def test_nonexistent_toml_should_raise_filenotfound_when_loaded(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_work_dir_dot_should_resolve_project_to_cwd_basename_when_loaded(
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


def test_agent_name_in_toml_should_set_name_when_loaded(tmp_path: Path) -> None:
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


def test_agent_without_name_should_have_none_name_when_loaded(tmp_path: Path) -> None:
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


def test_injection_mode_explicit_should_set_mode_when_loaded(
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


def test_injection_mode_absent_should_default_to_prepend_when_loaded(
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


def test_invalid_injection_mode_should_raise_when_loaded(tmp_path: Path) -> None:
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


def test_no_monitor_block_should_use_default_patterns_when_loaded(
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


def test_custom_auth_patterns_should_be_used_when_loaded(tmp_path: Path) -> None:
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


def test_no_plugins_block_should_default_disable_and_raw_to_empty_when_loaded(
    tmp_path: Path,
) -> None:
    from agent_runner.config import PluginsConfig

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

    assert isinstance(cfg.plugins, PluginsConfig)
    assert cfg.plugins.disable == [] and cfg.plugins.raw == {}


def test_plugins_block_should_pass_through_when_present(
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


def test_no_auto_stop_on_should_default_to_builtins_when_loaded(
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


def test_custom_auto_stop_on_should_be_used_when_loaded(tmp_path: Path) -> None:
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


def test_bare_string_auto_stop_on_should_raise_config_error_when_loaded(tmp_path: Path) -> None:
    """A bare string would silently list()-explode into single characters,
    which never match a real detector name -- auto-stop for that detector
    goes silently dark with no error. Must be rejected, not accepted."""
    from agent_runner.config import ConfigError

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
auto_stop_on = "oauth_fail"
""",
    )

    with pytest.raises(ConfigError, match="monitor.auto_stop_on"):
        load_config(toml)


def test_agent_env_block_should_populate_env_when_loaded(tmp_path: Path) -> None:
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


def test_no_agent_env_block_should_default_env_to_empty_dict_when_loaded(
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


def test_agent_env_non_string_values_should_be_coerced_to_str_when_loaded(
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


def test_no_auth_fail_hint_in_toml_should_default_to_empty_string_when_loaded(
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


def test_round_timeout_per_phase_dict_should_raise_config_error_with_migration_hint_when_loaded(
    tmp_path: Path,
) -> None:
    """Old runtime.round_timeout_per_phase = {...} syntax → ConfigError with migration path."""
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


def test_runtime_config_should_have_no_round_timeout_per_phase_field() -> None:
    import dataclasses

    from agent_runner.config import RuntimeConfig

    field_names = {f.name for f in dataclasses.fields(RuntimeConfig)}

    assert "round_timeout_per_phase" not in field_names


def test_round_timeout_s_bool_should_raise_value_error_when_loaded(
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


def test_restart_delay_s_zero_should_raise_value_error_when_loaded(
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
restart_delay_s = 0
[prompt]
file = "prompts/main.md"
""",
    )

    with pytest.raises(ValueError, match="restart_delay_s.*must be positive"):
        load_config(toml)


def test_stash_idempotency_s_float_should_raise_value_error_when_loaded(
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
[vcs]
stash_idempotency_s = 1.5
""",
    )

    with pytest.raises(ValueError, match="stash_idempotency_s.*must be an integer"):
        load_config(toml)


def test_no_monitor_block_should_default_remote_failure_tolerance_to_90_when_loaded(
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

    assert cfg.monitor.remote_failure_tolerance_s == 90


def test_custom_remote_failure_tolerance_should_be_parsed_when_loaded(tmp_path: Path) -> None:
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


def test_zero_remote_failure_tolerance_should_be_accepted_as_opt_out_when_loaded(
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


def test_negative_remote_failure_tolerance_should_raise_when_loaded(tmp_path: Path) -> None:
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


def test_excessive_remote_failure_tolerance_should_raise_when_loaded(tmp_path: Path) -> None:
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


def test_plugins_disable_list_should_be_parsed_when_loaded(
    tmp_path: Path,
) -> None:
    """[plugins] disable = [...] is parsed into PluginsConfig.disable.

    The names don't match any registered plugin in test env, so apply_plugin_disable
    emits a UserWarning — assert it explicitly.
    """
    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path)
        + '\n[plugins]\ndisable = ["acme_prompt_assembly", "acme_chain_state"]\n',
    )

    with pytest.warns(UserWarning, match="acme_prompt_assembly"):
        cfg = load_config(cfg_path)

    assert cfg.plugins.disable == ["acme_prompt_assembly", "acme_chain_state"]
    assert cfg.plugins.raw == {}


def test_bare_string_plugins_disable_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A bare string would silently list()-explode into single-character
    "plugin names", never matching the intended plugin -- disable() becomes a
    silent no-op. Must be rejected, not accepted."""
    from agent_runner.config import ConfigError

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path)
        + '\n[plugins]\ndisable = "acme_prompt_assembly"\n',
    )

    with pytest.raises(ConfigError, match="plugins.disable"):
        load_config(cfg_path)


def test_plugins_unknown_keys_should_be_preserved_in_raw_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path) + '\n[plugins]\nacme_foo = "bar"\n',
    )

    cfg = load_config(cfg_path)

    assert cfg.plugins.disable == []
    assert cfg.plugins.raw == {"acme_foo": "bar"}


def test_no_disable_hooks_should_default_to_false_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(tmp_path, _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path))

    cfg = load_config(cfg_path)

    assert cfg.runtime.disable_pre_round_hooks is False


def test_disable_hooks_true_should_be_honored_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    body = _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path).replace(
        "[runtime]",
        "[runtime]\ndisable_pre_round_hooks = true",
    )
    cfg_path = _write_toml(tmp_path, body)

    cfg = load_config(cfg_path)

    assert cfg.runtime.disable_pre_round_hooks is True


def test_disable_hooks_non_bool_should_raise_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    body = _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path).replace(
        "[runtime]",
        "[runtime]\ndisable_pre_round_hooks = 'yes'",
    )
    cfg_path = _write_toml(tmp_path, body)

    with pytest.raises(ValueError, match="must be a bool"):
        load_config(cfg_path)


def test_default_runtime_should_have_round_log_retention_zero_when_loaded(
    tmp_path: Path,
) -> None:
    """Pruning is opt-in: round_log_retention defaults to 0 (never prune)."""
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

    assert cfg.runtime.round_log_retention == 0
    assert cfg.runtime.narrative_file is None


def test_explicit_zero_round_log_retention_should_be_accepted_when_loaded(
    tmp_path: Path,
) -> None:
    """0 is a legal value with a meaning (never prune), not a rejected input.

    The question this answers: "is there a value that disables pruning?"
    """
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "round_log_retention = 0\n"
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")

    assert cfg.runtime.round_log_retention == 0


def test_negative_round_log_retention_should_be_rejected_when_loaded(
    tmp_path: Path,
) -> None:
    """Negative is still meaningless — rejected at load, not silently coerced."""
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "round_log_retention = -1\n"
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    with pytest.raises(ValueError, match="round_log_retention: must be >= 0"):
        load_config(tmp_path / "agent-runner.toml")


def test_explicit_round_log_retention_should_be_used_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_explicit_narrative_file_should_resolve_to_path_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_phases_list_only_should_yield_empty_overrides_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_phase_sub_table_round_timeout_should_be_recorded_as_override_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_phase_sub_table_all_three_fields_should_all_be_parsed_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_phase_name_not_in_list_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_unknown_field_in_phase_sub_table_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_no_phases_section_should_yield_none_list_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_prompt_files_list_should_set_files_attribute_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_prompt_file_single_should_be_accepted_for_back_compat_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_both_prompt_file_and_files_set_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_custom_concat_separator_should_be_used_when_loaded(tmp_path: Path) -> None:
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


def test_strip_yaml_frontmatter_false_should_be_honored_when_loaded(
    tmp_path: Path,
) -> None:
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


@pytest.mark.parametrize("value", ["stash", "ignore", "auto_commit"])
def test_dirty_action_valid_value_should_set_field_when_loaded(tmp_path: Path, value: str) -> None:
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
        f'dirty_action = "{value}"\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")

    assert cfg.vcs.dirty_action == value


def test_dirty_action_invalid_value_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_orphan_action_in_toml_should_raise_with_migration_hint_when_loaded(
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


def test_relative_work_dir_should_anchor_to_config_parent_when_loaded_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """work_dir = "." anchors to the config file's directory, never the caller's cwd —
    otherwise `agent-runner round --config /abs/proj/...` run from $HOME would drive
    the wrong tree. Paths derived from work_dir follow it."""
    proj = tmp_path / "proj"
    (proj / "prompts").mkdir(parents=True)
    (proj / "prompts" / "main.md").write_text("p")
    cfg_path = proj / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        'work_dir = "."\n'
        'log_dir = "logs"\n'
        "[prompt]\n"
        'file = "./prompts/main.md"\n'
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(cfg_path)

    assert cfg.runtime.work_dir == proj.resolve()
    assert cfg.prompt.file == (proj / "prompts" / "main.md").resolve()
    assert cfg.runtime.log_dir == (proj / "logs").resolve()


def test_absolute_work_dir_should_remain_unchanged_when_loaded_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute work_dir is independent of both the cwd and the config's location."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "prompt.md").write_text("p")
    cfg_path = tmp_path / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{proj}"\n'
        'log_dir = "logs"\n'
        "[prompt]\n"
        'file = "prompt.md"\n'
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(cfg_path)

    assert cfg.runtime.work_dir == proj.resolve()
    assert cfg.prompt.file == (proj / "prompt.md").resolve()


def test_relative_log_dir_should_resolve_to_absolute_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_relative_narrative_file_should_resolve_to_absolute_when_loaded(tmp_path: Path) -> None:
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


def test_relative_prompt_file_should_resolve_to_absolute_when_loaded(tmp_path: Path) -> None:
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


def test_relative_prompt_files_list_should_all_resolve_to_absolute_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_relative_per_phase_prompt_files_should_resolve_to_absolute_when_loaded(
    tmp_path: Path,
) -> None:
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


def test_no_narrative_file_should_remain_none_when_loaded(tmp_path: Path) -> None:
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


def test_rate_limit_action_in_toml_should_raise_config_error_with_migration_hint(tmp_path):
    """0.1.29: alias removed. TOML containing rate_limit_action must error."""
    from agent_runner.config import ConfigError

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


def test_transient_error_action_should_still_be_accepted(tmp_path):
    """Sanity: canonical key still works post-alias-removal."""
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


def test_runtime_dry_run_should_load_from_toml(tmp_path):
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


def test_runtime_dry_run_should_default_to_false(tmp_path):
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


def test_monitor_host_health_defaults_should_match_detector_defaults(
    tmp_path: Path,
) -> None:
    """Regression: MonitorHostHealthConfig defaults must match detector hardcoded thresholds."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.mem_avail_min_mb == 200
    assert cfg.disk_warning_pct == 90.0
    assert cfg.disk_critical_pct == 95.0


def test_monitor_host_health_toml_section_should_apply_overrides_when_loaded(
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


def test_host_health_floor_defaults_should_match_hardcoded_constants() -> None:
    """Defaults must stay byte-identical to the previous hardcoded constants
    (32 MiB swap-out noise floor, 16 MB MemFree floor) -- existing deployments
    are unaffected unless the operator explicitly sets these fields."""
    from agent_runner.config import MonitorHostHealthConfig

    hh = MonitorHostHealthConfig()

    assert hh.swap_sout_noise_floor_mb == 32
    assert hh.mem_free_low_mb == 16


def test_host_health_floors_should_parse_from_toml(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        '[agent]\ncommand = ["claude"]\nname = "claude"\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n\n'
        f'[runtime]\nwork_dir = "."\nlog_dir = "{tmp_path}/logs"\n\n'
        f'[prompt]\nfile = "{prompt_file}"\n\n'
        "[monitor.host_health]\nswap_sout_noise_floor_mb = 8\nmem_free_low_mb = 4\n",
        encoding="utf-8",
    )

    cfg = load_config(toml)

    assert cfg.monitor.host_health.swap_sout_noise_floor_mb == 8
    assert cfg.monitor.host_health.mem_free_low_mb == 4


@pytest.mark.parametrize("field", ["swap_sout_noise_floor_mb", "mem_free_low_mb"])
def test_host_health_floors_should_reject_zero_and_non_int_values(
    tmp_path: Path, field: str
) -> None:
    """0 would silently disable/invert the floor (delta > 0 is always true) --
    must be rejected via _require_positive_int, not accepted as a no-op."""
    from agent_runner.config import ConfigError

    toml_zero = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        f"[monitor.host_health]\n{field} = 0\n",
    )

    with pytest.raises(ConfigError, match=f"monitor.host_health.{field}"):
        load_config(toml_zero)

    toml_str = _write_toml(
        tmp_path,
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        f'[monitor.host_health]\n{field} = "x"\n',
    )

    with pytest.raises(ConfigError, match=f"monitor.host_health.{field}"):
        load_config(toml_str)


def test_custom_mem_threshold_in_config_should_be_used_when_detect_mem_pressure_runs(
    tmp_path: Path,
) -> None:
    from agent_runner.config import MonitorConfig, MonitorHostHealthConfig
    from agent_runner.monitor import detect_mem_pressure

    host_health = MonitorHostHealthConfig(mem_avail_min_mb=500)
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
    from agent_runner.monitor import run_all_detectors

    # mem_available_mb=300: below custom mem_avail_min_mb=500 but above default 200;
    # mem_free_mb=5 (low) makes it a genuine combined-low signal, not silence.
    metrics = [{"mem_available_mb": 300, "mem_free_mb": 5, "disk_used_pct": 92.0}]

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


def test_run_all_detectors_should_thread_custom_floors_into_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: run_all_detectors must plumb swap_sout_noise_floor_mb and
    mem_free_low_mb into the MonitorHostHealthConfig it builds and passes to
    detect_mem_pressure -- pre-fix these floors were config-tunable in TOML but
    silently ignored on this path (mirrors the mem_avail_min_mb/disk_* wiring
    regression test above)."""
    from agent_runner import host_health, monitor

    seen: dict[str, int] = {}
    real = host_health.memory_pressure

    def spy(sample, prev, cfg):
        seen["swap_floor"] = cfg.swap_sout_noise_floor_mb
        seen["mem_free"] = cfg.mem_free_low_mb
        return real(sample, prev, cfg)

    monkeypatch.setattr(host_health, "memory_pressure", spy)
    metrics = [{"mem_free_mb": 500, "mem_available_mb": 500, "swap_sout": 0}]

    monitor.run_all_detectors(
        events=[],
        metrics=metrics,
        log_tails={},
        swap_sout_noise_floor_mb=8,
        mem_free_low_mb=4,
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


def test_grace_kill_ignore_patterns_should_default_to_empty(tmp_path: Path) -> None:
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        '[prompt]\nfile="p.md"\n'
    )

    cfg = load_config(toml)

    assert cfg.runtime.grace_kill_ignore_patterns == []


def test_grace_kill_ignore_patterns_should_be_parsed_from_toml(tmp_path: Path) -> None:
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
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
        '[agent]\ncommand=["true"]\nprompt_arg_template=["-p","{prompt}"]\n'
        '[runtime]\nwork_dir="."\nlog_dir="logs"\n'
        'grace_kill_ignore_patterns = ["[unclosed"]\n'
        '[prompt]\nfile="p.md"\n'
    )

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert "grace_kill_ignore_patterns" in str(exc.value)


_HOST_HEALTH_BASE = """\
[agent]
command = ["true"]
prompt_arg_template = ["-p", "{prompt}"]
[runtime]
work_dir = "."
log_dir = "logs"
[prompt]
file = "p.md"
[monitor.host_health]
"""


@pytest.mark.parametrize("field", ["disk_warning_pct", "disk_critical_pct"])
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
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"{field} = {literal}\n")

    with pytest.raises(ValueError) as exc:
        load_config(toml)

    assert f"monitor.host_health.{field}" in str(exc.value)


@pytest.mark.parametrize("field", ["disk_warning_pct", "disk_critical_pct"])
@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("95.0", 95.0),
        ("90", 90.0),  # TOML parses a bare int; both int and float literals accepted
        ("0", 0.0),
        ("100", 100.0),
    ],
)
def test_host_health_pct_field_should_accept_valid_values(
    tmp_path: Path, field: str, literal: str, expected: float
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"{field} = {literal}\n")

    assert getattr(load_config(toml).monitor.host_health, field) == expected


def test_host_health_defaults_should_be_used_when_section_absent(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE)

    cfg = load_config(toml)

    assert cfg.monitor.host_health.disk_warning_pct == 90.0
    assert cfg.monitor.host_health.disk_critical_pct == 95.0


def test_psi_thresholds_should_have_expected_defaults() -> None:
    """Default critical raised 1.0 -> 60.0 (0.2.15's 1% hiccup killed every
    round on a 462MB Pi; 60 matches systemd-oomd's DefaultMemoryPressureLimit,
    coma-onset rather than a swap hiccup)."""
    from agent_runner.config import MonitorHostHealthConfig

    assert MonitorHostHealthConfig().psi_full_avg10_critical == 60.0
    assert MonitorHostHealthConfig().psi_some_avg10_warning == 5.0


def test_psi_thresholds_should_apply_custom_critical_override_when_set(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + "psi_full_avg10_critical = 75\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.psi_full_avg10_critical == 75.0
    assert cfg.monitor.host_health.psi_some_avg10_warning == 5.0  # still default


@pytest.mark.parametrize("field", ["psi_full_avg10_critical", "psi_some_avg10_warning"])
@pytest.mark.parametrize("bad", ["0", "101", '"x"'])
def test_host_health_psi_threshold_should_reject_out_of_range_value(
    tmp_path: Path, field: str, bad: str
) -> None:
    """0 is rejected (not just accepted-as-boundary like the disk pct fields):
    a PSI threshold of 0 fires on any measurable reading (psi_full/some >= 0
    is always true) -- the same hiccup-not-coma footgun this release exists
    to fix, at the opposite extreme. See _require_positive_pct."""
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"{field} = {bad}\n")

    with pytest.raises(ValueError, match=f"monitor.host_health.{field}"):
        load_config(toml)


@pytest.mark.parametrize("field", ["psi_full_avg10_critical", "psi_some_avg10_warning"])
@pytest.mark.parametrize(("literal", "expected"), [("100", 100.0), ("0.5", 0.5), ("60", 60.0)])
def test_host_health_psi_threshold_should_accept_in_range_value(
    tmp_path: Path, field: str, literal: str, expected: float
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"{field} = {literal}\n")

    assert getattr(load_config(toml).monitor.host_health, field) == expected


def test_mid_round_hysteresis_should_default_to_three_samples_with_terminate_enabled() -> None:
    """Defaults: 3 consecutive critical samples required, off switch defaults on
    (existing deployments keep terminating, just with hysteresis now applied)."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.mem_critical_consecutive_samples == 3
    assert cfg.in_round_mem_terminate is True


def test_mem_critical_consecutive_samples_should_parse_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + "mem_critical_consecutive_samples = 5\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.mem_critical_consecutive_samples == 5


@pytest.mark.parametrize("bad", ["0", "-1", '"x"', "true"])
def test_mem_critical_consecutive_samples_should_reject_invalid_values(
    tmp_path: Path, bad: str
) -> None:
    """Must be a positive int -- 0 or negative would terminate on the very
    first (or never) sample, defeating the hysteresis this field exists for."""
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"mem_critical_consecutive_samples = {bad}\n")

    with pytest.raises(ValueError, match="monitor.host_health.mem_critical_consecutive_samples"):
        load_config(toml)


@pytest.mark.parametrize(("literal", "expected"), [("true", True), ("false", False)])
def test_in_round_mem_terminate_should_parse_bool_value(
    tmp_path: Path, literal: str, expected: bool
) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"in_round_mem_terminate = {literal}\n")

    cfg = load_config(toml)

    assert cfg.monitor.host_health.in_round_mem_terminate is expected


@pytest.mark.parametrize("bad", ["1", '"true"'])
def test_in_round_mem_terminate_should_reject_non_bool_value(tmp_path: Path, bad: str) -> None:
    toml = _write_toml(tmp_path, _HOST_HEALTH_BASE + f"in_round_mem_terminate = {bad}\n")

    with pytest.raises(ValueError, match="monitor.host_health.in_round_mem_terminate"):
        load_config(toml)


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


def test_removed_field_error_should_point_to_migrate_command(tmp_path):
    """Removed-field ConfigErrors must direct users to `agent-runner migrate`."""
    from agent_runner.config import ConfigError

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
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


# --- 0.2.13 strictness completion: base-table unknown keys, [phases] scalar
# keys, per-phase [phases.<name>.prompt] unknown keys, argv prompt_arg_template
# missing {prompt} (with the per-phase files=[] carve-out) ---


def _min_config_lines(
    tmp_path: Path, *, agent_extra: str = "", runtime_extra: str = ""
) -> list[str]:
    """The minimal valid [agent]/[runtime]/[prompt] lines this module's other
    tests inline repeatedly, with room to inject an extra raw line into
    [agent]/[runtime]. Callers append their own trailing [table] blocks."""
    return [
        "[agent]\n",
        'command = ["true"]\n',
        'prompt_arg_template = ["{prompt}"]\n',
        agent_extra,
        "[runtime]\n",
        f'work_dir = "{tmp_path}"\n',
        f'log_dir = "{tmp_path}/logs"\n',
        runtime_extra,
        "[prompt]\n",
        f'file = "{tmp_path}/prompt.md"\n',
    ]


@pytest.mark.parametrize(
    "table, extra",
    [
        ("agent", "agent_extra"),
        ("runtime", "runtime_extra"),
        ("vcs", None),
        ("monitor", None),
    ],
)
def test_unknown_base_table_key_should_raise_config_error_when_loaded(
    tmp_path: Path, table: str, extra: str | None
) -> None:
    """Unknown keys under base [agent]/[runtime]/[vcs]/[monitor] used to load
    silently; 0.2.13 rejects them like the existing [prompt]/[schedule] checks."""
    from agent_runner.config import ConfigError

    (tmp_path / "prompt.md").write_text("p")
    kwargs = {extra: 'bogus_field = "x"\n'} if extra else {}
    lines = _min_config_lines(tmp_path, **kwargs)
    if extra is None:
        lines.append(f'[{table}]\nbogus_field = "x"\n')
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=rf"unknown \[{table}\].*bogus_field"):
        load_config(tmp_path / "agent-runner.toml")


def test_scalar_key_under_phases_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A stray non-table key under [phases] (typo'd out of a [phases.<name>]
    sub-table) used to be silently skipped; 0.2.13 rejects it."""
    from agent_runner.config import ConfigError

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append('[phases]\nlist = ["dev"]\nbogus_field = "x"\n[phases.dev]\n')
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"\[phases\].*bogus_field"):
        load_config(tmp_path / "agent-runner.toml")


def test_unknown_per_phase_prompt_key_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """[phases.<name>.prompt] only ever reads 'files'; migrations/0.2.md already
    promised rejection of anything else — 0.2.13 makes the code match."""
    from agent_runner.config import ConfigError

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["dev"]\n'
        "[phases.dev.prompt]\n"
        'files = ["dev.md"]\n'
        "inject_context = false\n"
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"phases\.dev\.prompt.*inject_context"):
        load_config(tmp_path / "agent-runner.toml")


def test_argv_prompt_arg_template_should_raise_config_error_when_placeholder_missing(
    tmp_path: Path,
) -> None:
    """argv delivery with no {prompt} token in the template never delivers the
    prompt to the agent — a silent footgun 0.2.13 now rejects."""
    from agent_runner.config import ConfigError
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra='prompt_arg_template = ["-p"]\n')

    with pytest.raises(ConfigError, match=r"prompt_arg_template.*\{prompt\}"):
        load_config(cfg_path)


def test_disabled_phase_agent_should_be_accepted_without_placeholder_when_loaded(
    tmp_path: Path,
) -> None:
    """Carve-out: a phase whose own prompt.files = [] sends no prompt at all,
    so its own [phases.<name>.agent] template needs no {prompt} token."""
    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["silent"]\n'
        "[phases.silent.prompt]\n"
        "files = []\n"
        "[phases.silent.agent]\n"
        'prompt_arg_template = ["--silent-mode"]\n'
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    cfg = load_config(tmp_path / "agent-runner.toml")

    assert cfg.phases.overrides["silent"].agent.prompt_arg_template == ["--silent-mode"]


def test_enabled_phase_agent_should_raise_config_error_without_placeholder_when_loaded(
    tmp_path: Path,
) -> None:
    """Without the files=[] carve-out, a phase inherits the base's (mandatory,
    non-empty) prompt, so its own argv template still needs {prompt}."""
    from agent_runner.config import ConfigError

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["silent"]\n'
        "[phases.silent.agent]\n"
        'prompt_arg_template = ["--silent-mode"]\n'
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"prompt_arg_template.*\{prompt\}"):
        load_config(tmp_path / "agent-runner.toml")


def test_load_config_should_parse_exec_prefix_when_set_on_base_agent(tmp_path: Path) -> None:
    """exec_prefix is opaque operator-supplied argv (e.g. a container-run prefix);
    the base [agent] table parses it into AgentConfig verbatim."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra='exec_prefix = ["docker", "run", "img"]\n')

    cfg = load_config(cfg_path)

    assert cfg.agent.exec_prefix == ["docker", "run", "img"]


def test_load_config_should_reject_exec_prefix_when_set_on_a_phase_agent(tmp_path: Path) -> None:
    """exec_prefix is base-only: setting it under [phases.<name>.agent] would let
    phases silently disagree on the container/runtime, defeating the SSOT."""
    from agent_runner.config import ConfigError
    from tests._test_helpers import make_toml_with_sections

    cfg_path = make_toml_with_sections(
        tmp_path,
        phases_block=(
            '[phases]\nlist = ["a"]\n[phases.a.agent]\nexec_prefix = ["docker","run","img"]\n'
        ),
    )

    with pytest.raises(ConfigError, match="exec_prefix.*base"):
        load_config(cfg_path)
