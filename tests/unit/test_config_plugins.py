from __future__ import annotations

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


def test_plugins_unknown_keys_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:

    (tmp_path / "prompt.md").write_text("p")
    cfg_path = _write_toml(
        tmp_path,
        _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path) + '\n[plugins]\nacme_foo = "bar"\n',
    )

    with pytest.raises(ConfigError) as caught:
        load_config(cfg_path)

    actual = str(caught.value)
    assert "unknown [plugins] field" in actual
    assert "acme_foo" in actual


def test_disable_pre_round_hooks_should_be_rejected_as_unknown_key_when_loaded(
    tmp_path: Path,
) -> None:
    """0.3.9: disable_pre_round_hooks's only consumer (the PreRoundHook plugin
    seam) was removed. The field is gone from RuntimeConfig, so an old config
    still carrying it now trips the generic unknown-[runtime]-key rejection —
    not silently ignored, not still honored. `agent-runner migrate` drops it
    (see tests/unit/test_migrations.py)."""
    (tmp_path / "prompt.md").write_text("p")

    body = _MINIMAL_TOML_NO_PLUGINS.format(tmp_path=tmp_path).replace(
        "[runtime]",
        "[runtime]\ndisable_pre_round_hooks = true",
    )
    cfg_path = _write_toml(tmp_path, body)

    with pytest.raises(ConfigError) as caught:
        load_config(cfg_path)

    actual = str(caught.value)
    assert "unknown [runtime] field" in actual
    assert "disable_pre_round_hooks" in actual


def test_default_runtime_should_have_round_log_retention_zero_when_loaded(
    tmp_path: Path,
) -> None:
    """Pruning is opt-in: round_log_retention defaults to 0 (never prune)."""
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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

    with pytest.raises(ValueError) as caught:
        load_config(tmp_path / "agent-runner.toml")

    actual = str(caught.value)
    assert "round_log_retention: must be >= 0" in actual


def test_explicit_round_log_retention_should_be_used_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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


def test_phase_sub_table_round_budget_should_be_recorded_as_override_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "round_budget_s = 3600\n"
    )

    cfg = load_config(tmp_path / "agent-runner.toml")

    assert cfg.phases.overrides["dev"].round_budget_s == 3600
    assert cfg.phases.overrides["dev"].prompt_files is None


def test_phase_sub_table_both_fields_should_be_parsed_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "round_budget_s = 3600\n"
        'prompt.files = ["a.md", "b.md"]\n'
    )

    cfg = load_config(tmp_path / "agent-runner.toml")

    o = cfg.phases.overrides["dev"]
    assert o.round_budget_s == 3600
    assert o.prompt_files == [tmp_path / "a.md", tmp_path / "b.md"]


def test_phase_name_not_in_list_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "round_budget_s = 3600\n"
    )

    with pytest.raises(ValueError) as caught:
        load_config(tmp_path / "agent-runner.toml")

    actual = str(caught.value)
    assert "[phases.foo]" in actual
    assert "not in phases.list" in actual


def test_unknown_field_in_phase_sub_table_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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

    with pytest.raises(ValueError) as caught:
        load_config(tmp_path / "agent-runner.toml")

    actual = str(caught.value)
    assert "unknown per-phase field" in actual
    assert "made_up_field" in actual


def test_no_phases_section_should_yield_none_list_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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

    with pytest.raises(ValueError) as caught:
        load_config(tmp_path / "agent-runner.toml")

    actual = str(caught.value)
    assert "prompt.file" in actual
    assert "prompt.files" in actual
    assert "not both" in actual


def test_custom_concat_separator_should_be_used_when_loaded(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "agent-runner.toml").write_text(
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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

    with pytest.raises(ValueError) as caught:
        load_config(tmp_path / "agent-runner.toml")

    actual = str(caught.value)
    assert "vcs.dirty_action" in actual
    assert "explode" in actual
    assert "stash" in actual


def test_orphan_action_in_toml_should_raise_with_migration_hint_when_loaded(
    tmp_path: Path,
) -> None:
    """vcs.orphan_action removed in 0.1.18 — TOML using it must raise with migration hint."""
    cfg_path = tmp_path / "agent-runner.toml"

    (tmp_path / "p.md").write_text("hi")
    cfg_path.write_text(
        "schema_version = 1\n"
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

    with pytest.raises(ValueError) as caught:
        load_config(cfg_path)

    actual = str(caught.value)
    assert "vcs.orphan_action removed in 0.1.18" in actual


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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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
        "schema_version = 1\n"
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


def test_rate_limit_action_in_toml_should_raise_config_error_with_migration_hint_when_invoked(
    tmp_path,
):
    """0.1.29: alias removed. TOML containing rate_limit_action must error."""

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
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


def test_transient_error_action_should_still_be_accepted_when_invoked(tmp_path):
    """Sanity: canonical key still works post-alias-removal."""
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
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


def test_runtime_dry_run_should_load_from_toml_when_invoked(tmp_path):
    toml = tmp_path / "agent-runner.toml"
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml.write_text(
        "schema_version = 1\n"
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


def test_runtime_dry_run_should_default_to_false_when_invoked(tmp_path):
    toml = tmp_path / "agent-runner.toml"
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x" * 800, encoding="utf-8")
    toml.write_text(
        "schema_version = 1\n"
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


def test_monitor_host_health_config_should_expose_grouped_defaults_when_constructed() -> None:
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig()

    assert cfg.disk.warning_pct == 90.0
    assert cfg.disk.critical_pct == 95.0
    assert cfg.memory.avail_min_mb == 200
    assert cfg.memory.free_low_mb == 16
    assert cfg.memory.swap_out_noise_floor_mb == 32
    assert cfg.pressure.full_avg10_critical == 60.0
    assert cfg.pressure.some_avg10_warning == 5.0
    assert cfg.pressure.critical_consecutive_samples == 3
    assert cfg.pressure.in_round_terminate is True
