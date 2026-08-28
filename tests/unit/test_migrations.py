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
