import tomllib

from agent_runner import migrations


def _run(text: str) -> migrations.MigrationResult:
    return migrations.run_migrations(text, tomllib.loads(text))


def test_rename_rate_limit_action_preserves_value_and_comment():
    text = '[runtime]\nrate_limit_action = "stop"   # keep on quota\n'
    r = _run(text)
    assert 'transient_error_action = "stop"   # keep on quota' in r.new_text
    assert "rate_limit_action" not in r.new_text
    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]
    assert r.manual == []


def test_rename_orphan_action():
    text = '[vcs]\norphan_action = "ignore"\n'
    r = _run(text)
    assert 'dirty_action = "ignore"' in r.new_text
    assert "orphan_action" not in r.new_text
    assert r.applied == ["vcs.orphan_action → vcs.dirty_action"]


def test_comment_line_is_not_rewritten():
    # A live key AND a commented-out one: the live line is renamed, the comment
    # is left byte-identical (the regex is anchored to real assignments).
    text = '[runtime]\n# rate_limit_action = "old"  historical note\nrate_limit_action = "stop"\n'
    r = _run(text)
    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]
    assert '# rate_limit_action = "old"  historical note' in r.new_text  # comment untouched
    assert 'transient_error_action = "stop"' in r.new_text


def test_manual_transform_detected_not_applied():
    text = "[runtime]\nround_timeout_per_phase = { dev = 900 }\n"
    r = _run(text)
    assert r.applied == []
    assert len(r.manual) == 1 and "round_timeout_per_phase" in r.manual[0]
    assert r.new_text == text  # manual transforms never touch the text


def test_rename_with_target_key_present_is_not_adopted():
    # Both the deprecated key AND the target key are set: a blind rename would
    # produce two `dirty_action` lines (invalid TOML). The rewrite must be
    # rejected and routed to manual, leaving the text untouched.
    text = '[vcs]\norphan_action = "ignore"\ndirty_action = "stash"\n'
    r = _run(text)
    assert r.applied == []
    assert len(r.manual) == 1 and "remove the deprecated" in r.manual[0]
    assert r.new_text == text  # invalid rewrite was not adopted


def test_current_config_is_noop():
    text = '[runtime]\ntransient_error_action = "back_off"\n[vcs]\ndirty_action = "stash"\n'
    r = _run(text)
    assert r.applied == [] and r.manual == [] and r.new_text == text


def test_flat_phase_override_detected_as_manual():
    # A flat round_timeout_s directly under [phases.a] must be reported for a
    # manual move under [phases.a.runtime] — never rewritten (a header rename
    # would silently re-parent sibling sub-tables like prompt.files).
    text = 'phases.list = ["a"]\n[phases.a]\nround_timeout_s = 900\n'
    r = _run(text)
    assert r.applied == []
    assert len(r.manual) == 1 and "[phases.<name>.runtime]" in r.manual[0]
    assert r.new_text == text  # manual transforms never touch the text


def test_flat_phase_disable_hooks_detected():
    text = 'phases.list = ["a"]\n[phases.a]\ndisable_pre_round_hooks = true\n'
    r = _run(text)
    assert r.applied == []
    assert len(r.manual) == 1 and "[phases.<name>.runtime]" in r.manual[0]


def test_nested_phase_runtime_is_not_flagged():
    text = 'phases.list = ["a"]\n[phases.a.runtime]\nround_timeout_s = 900\n'
    r = _run(text)
    assert r.applied == [] and r.manual == [] and r.new_text == text


def test_rename_is_table_scoped_leaves_other_tables():
    # A plugin sub-table with the same key name must NOT be renamed — only the
    # real [runtime] one. (Regression: the pre-0.2.12 rename was table-blind.)
    text = '[runtime]\nrate_limit_action = "stop"\n[plugins.foo]\nrate_limit_action = "keep"\n'
    r = _run(text)
    assert 'transient_error_action = "stop"' in r.new_text
    assert '[plugins.foo]\nrate_limit_action = "keep"' in r.new_text  # untouched
    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]


def test_wrap_bare_command_single_token():
    text = '[agent]\ncommand = "claude"   # main agent\n'
    r = _run(text)
    assert 'command = ["claude"]   # main agent' in r.new_text
    assert r.manual == []
    assert any("command" in a for a in r.applied)


def test_space_command_is_manual_not_split():
    text = '[agent]\ncommand = "claude -p"\n'
    r = _run(text)
    assert r.applied == []
    assert any("argv list" in m for m in r.manual)
    assert r.new_text == text  # never auto-split quoted argv


def test_wrap_bare_phases_list():
    text = '[phases]\nlist = "dev"\n'
    r = _run(text)
    assert 'list = ["dev"]' in r.new_text
    assert r.manual == []


def test_wrap_bare_top_level_prompt_files():
    text = '[prompt]\nfiles = "main.md"\n'
    r = _run(text)
    assert 'files = ["main.md"]' in r.new_text


def test_wrap_bare_monitor_auto_stop_on():
    text = '[monitor]\nauto_stop_on = "oauth_fail"\n'
    r = _run(text)
    assert 'auto_stop_on = ["oauth_fail"]' in r.new_text
    assert r.manual == []


def test_wrap_bare_plugins_disable():
    text = '[plugins]\ndisable = "my_plugin"\n'
    r = _run(text)
    assert 'disable = ["my_plugin"]' in r.new_text
    assert r.manual == []


def test_unknown_prompt_key_is_manual():
    text = '[prompt]\nfile = "x.md"\nbogus = 1\n'
    r = _run(text)
    assert any("bogus" in m and "[prompt]" in m for m in r.manual)
    assert r.new_text == text


def test_unknown_schedule_key_is_manual():
    text = '[schedule]\ntimezone = "UTC"\nnope = 1\n'
    r = _run(text)
    assert any("nope" in m and "[schedule]" in m for m in r.manual)


def test_threshold_gt_window_is_manual():
    text = "[monitor]\nanomaly_repetitive_window = 3\nanomaly_repetitive_threshold = 5\n"
    r = _run(text)
    assert any("anomaly_repetitive_threshold" in m for m in r.manual)


def test_empty_command_is_manual():
    text = "[agent]\ncommand = []\n"
    r = _run(text)
    assert r.applied == []
    assert any("command" in m and "empty" in m for m in r.manual)  # no auto-fix: real value needed


def test_empty_top_level_prompt_files_is_manual():
    text = "[prompt]\nfiles = []\n"
    r = _run(text)
    assert r.applied == []
    assert any("[prompt] files" in m for m in r.manual)


def test_wrap_bare_per_phase_command_and_prompt_files():
    text = (
        '[phases]\nlist = ["dev"]\n'
        '[phases.dev.agent]\ncommand = "claude"\n'
        '[phases.dev.prompt]\nfiles = "dev.md"\n'
    )
    r = _run(text)
    assert 'command = ["claude"]' in r.new_text
    assert 'files = ["dev.md"]' in r.new_text
    assert r.manual == []


def test_unknown_phase_schedule_key_is_manual():
    text = '[phases]\nlist = ["dev"]\n[phases.dev.schedule]\nnope = 1\n'
    r = _run(text)
    assert any("schedule" in m for m in r.manual)


def test_empty_per_phase_command_is_manual():
    # A per-phase [phases.<name>.agent] command = [] merges onto the base
    # [agent] table and overrides it to empty, hitting the same "non-empty
    # list" rejection as a top-level empty command — needs its own entry since
    # detecting it requires walking [phases.<name>.agent] sub-tables.
    text = '[phases]\nlist = ["dev"]\n[phases.dev.agent]\ncommand = []\n'
    r = _run(text)
    assert r.applied == []
    assert any("phases.<name>.agent" in m and "empty" in m for m in r.manual)
    assert r.new_text == text


def test_wrap_bare_per_phase_command_does_not_touch_sibling_space_command():
    # Regression: the per-phase [phases.<name>.agent] walker used to wrap EVERY
    # matching table unconditionally, so [phases.dev]'s safe bare command
    # tripping the auto-fix migration would ALSO silently auto-wrap
    # [phases.prod]'s space-bearing (unsafe-to-split) command into a
    # single-token argv list — a corrupted-but-technically-valid config that
    # the manual report simultaneously (and misleadingly) claimed was untouched.
    text = (
        '[phases]\nlist = ["dev", "prod"]\n'
        '[phases.dev.agent]\ncommand = "claude"\n'
        '[phases.prod.agent]\ncommand = "claude -p"\n'
    )
    r = _run(text)
    assert 'command = ["claude"]' in r.new_text  # dev: safe, auto-fixed
    assert '[phases.prod.agent]\ncommand = "claude -p"' in r.new_text  # prod: untouched
    assert any("argv list" in m for m in r.manual)
