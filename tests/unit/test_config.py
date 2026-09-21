from __future__ import annotations

import re
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
    assert cfg.runtime.round_budget_s == 1800  # default
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

    with pytest.raises(FileNotFoundError) as caught:
        load_config(tmp_path / "nope.toml")

    assert caught.value is not None


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

    with pytest.raises(ValueError, match="context_injection_mode") as caught:
        load_config(toml)

    assert re.search(r"context_injection_mode", str(caught.value))


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


def test_no_plugins_block_should_default_disable_to_empty_when_loaded(
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
    assert cfg.plugins.disable == []


def test_plugins_block_should_raise_when_key_misspelled(
    tmp_path: Path,
) -> None:
    """A typo'd key (`disabled` for `disable`) used to vanish silently into
    `.raw`; it must now be rejected the same as any other unknown field."""

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

    with pytest.raises(ConfigError, match=r"unknown \[plugins\] field.*disabled"):
        load_config(toml)


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


def test_goal_prefixed_auto_stop_on_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A goal-steering kind can never opt into the auto-stop kill path --
    the advisory goal_check/goal_assessment path must stay structurally
    unable to reach a kill decision (see
    tests/invariants/test_goal_firewall.py)."""

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
auto_stop_on = ["oauth_fail", "goal_check"]
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
        "schema_version = 1\n"
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


def test_runtime_config_should_have_no_round_timeout_per_phase_field_when_invoked() -> None:
    import dataclasses

    from agent_runner.config import RuntimeConfig

    field_names = {f.name for f in dataclasses.fields(RuntimeConfig)}

    assert "round_timeout_per_phase" not in field_names


def test_round_budget_s_bool_should_raise_value_error_when_loaded(
    tmp_path: Path,
) -> None:
    """Apply same type-guard to runtime.round_budget_s (was silently coercing)."""
    toml = _write_toml(
        tmp_path,
        """
[agent]
command = ["my-agent"]
prompt_arg_template = ["{prompt}"]
[runtime]
work_dir = "."
log_dir = "/tmp/logs"
round_budget_s = true
[prompt]
file = "prompts/main.md"
""",
    )

    with pytest.raises(ValueError, match="round_budget_s.*must be an integer") as caught:
        load_config(toml)

    assert re.search(r"round_budget_s.*must be an integer", str(caught.value))


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

    with pytest.raises(ValueError, match="must be >= 0") as caught:
        load_config(toml)

    assert re.search(r"must be >= 0", str(caught.value))


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


def test_bare_string_plugins_disable_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A bare string would silently list()-explode into single-character
    "plugin names", never matching the intended plugin -- disable() becomes a
    silent no-op. Must be rejected, not accepted."""

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path)
        + '\n[plugins]\ndisable = "acme_prompt_assembly"\n',
    )

    with pytest.raises(ConfigError, match="plugins.disable"):
        load_config(cfg_path)
