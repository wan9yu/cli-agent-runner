import tomllib

import pytest

from agent_runner import migrations


def _run(text: str) -> migrations.MigrationResult:
    return migrations.run_migrations(text, tomllib.loads(text))


def test_rate_limit_action_rename_should_preserve_value_and_comment():
    text = '[runtime]\nrate_limit_action = "stop"   # keep on quota\n'

    r = _run(text)

    assert 'transient_error_action = "stop"   # keep on quota' in r.new_text
    assert "rate_limit_action" not in r.new_text
    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]
    assert r.manual == []


def test_orphan_action_rename_should_replace_key_with_dirty_action():
    text = '[vcs]\norphan_action = "ignore"\n'

    r = _run(text)

    assert 'dirty_action = "ignore"' in r.new_text
    assert "orphan_action" not in r.new_text
    assert r.applied == ["vcs.orphan_action → vcs.dirty_action"]


def test_commented_out_key_should_stay_untouched_when_live_key_is_renamed():
    # A live key AND a commented-out one: the live line is renamed, the comment
    # is left byte-identical (the regex is anchored to real assignments).
    text = '[runtime]\n# rate_limit_action = "old"  historical note\nrate_limit_action = "stop"\n'

    r = _run(text)

    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]
    assert '# rate_limit_action = "old"  historical note' in r.new_text  # comment untouched
    assert 'transient_error_action = "stop"' in r.new_text


def test_round_timeout_per_phase_should_be_routed_to_manual_when_given_as_inline_table():
    text = "[runtime]\nround_timeout_per_phase = { dev = 900 }\n"

    r = _run(text)

    assert r.applied == []
    assert len(r.manual) == 1 and "round_timeout_per_phase" in r.manual[0]
    assert r.new_text == text  # manual transforms never touch the text


def test_orphan_action_rename_should_be_rejected_when_dirty_action_already_present():
    # Both the deprecated key AND the target key are set: a blind rename would
    # produce two `dirty_action` lines (invalid TOML). The rewrite must be
    # rejected and routed to manual, leaving the text untouched.
    text = '[vcs]\norphan_action = "ignore"\ndirty_action = "stash"\n'

    r = _run(text)

    assert r.applied == []
    assert len(r.manual) == 1 and "remove the deprecated" in r.manual[0]
    assert r.new_text == text  # invalid rewrite was not adopted


def test_up_to_date_config_should_be_a_noop():
    text = '[runtime]\ntransient_error_action = "back_off"\n[vcs]\ndirty_action = "stash"\n'

    r = _run(text)

    assert r.applied == [] and r.manual == [] and r.new_text == text


@pytest.mark.parametrize(
    "override_line",
    ["round_timeout_s = 900", "disable_pre_round_hooks = true"],
    ids=["round_timeout_s", "disable_pre_round_hooks"],
)
def test_flat_phase_override_should_be_advisory_when_set_directly(override_line: str):
    # A flat override directly under [phases.a] is guidance, not a rejection
    # — the flat form is a PERMANENT alias (config.py still loads it).
    # Routing it through `manual` used to make `upgrade` refuse to cross the
    # version boundary forever over a config that was never broken; it must
    # report as advisory and never block applied/manual-gated callers.
    text = f'phases.list = ["a"]\n[phases.a]\n{override_line}\n'

    r = _run(text)

    assert r.applied == []
    assert r.manual == []
    assert len(r.advisory) == 1 and "[phases.<name>.runtime]" in r.advisory[0]
    assert r.new_text == text  # advisory transforms never touch the text


def test_nested_phase_runtime_table_should_not_be_flagged():
    text = 'phases.list = ["a"]\n[phases.a.runtime]\nround_timeout_s = 900\n'

    r = _run(text)

    assert r.applied == [] and r.manual == [] and r.new_text == text


def test_rate_limit_action_rename_should_be_table_scoped_when_key_is_in_other_tables():
    # A plugin sub-table with the same key name must NOT be renamed — only the
    # real [runtime] one. (Regression: the pre-0.2.12 rename was table-blind.)
    text = '[runtime]\nrate_limit_action = "stop"\n[plugins.foo]\nrate_limit_action = "keep"\n'

    r = _run(text)

    assert 'transient_error_action = "stop"' in r.new_text
    assert '[plugins.foo]\nrate_limit_action = "keep"' in r.new_text  # untouched
    assert r.applied == ["runtime.rate_limit_action → runtime.transient_error_action"]


def test_bare_single_token_command_should_be_wrapped_into_argv_list():
    text = '[agent]\ncommand = "claude"   # main agent\n'

    r = _run(text)

    assert 'command = ["claude"]   # main agent' in r.new_text
    assert r.manual == []
    assert any("command" in a for a in r.applied)


def test_command_with_spaces_should_be_reported_manual_not_auto_split():
    text = '[agent]\ncommand = "claude -p"\n'

    r = _run(text)

    assert r.applied == []
    assert any("argv list" in m for m in r.manual)
    assert r.new_text == text  # never auto-split quoted argv


@pytest.mark.parametrize(
    "table,key,bare_value",
    [
        ("phases", "list", "dev"),
        ("prompt", "files", "main.md"),
        ("monitor", "auto_stop_on", "oauth_fail"),
        ("plugins", "disable", "my_plugin"),
    ],
    ids=["phases_list", "prompt_files", "monitor_auto_stop_on", "plugins_disable"],
)
def test_bare_scalar_should_be_wrapped_into_list_when_migrated(
    table: str, key: str, bare_value: str
):
    text = f'[{table}]\n{key} = "{bare_value}"\n'

    r = _run(text)

    assert f'{key} = ["{bare_value}"]' in r.new_text
    assert r.manual == []


def test_anomaly_repetitive_threshold_should_be_reported_manual_when_above_window():
    text = "[monitor]\nanomaly_repetitive_window = 3\nanomaly_repetitive_threshold = 5\n"

    r = _run(text)

    assert any("anomaly_repetitive_threshold" in m for m in r.manual)


def test_empty_command_should_be_reported_manual():
    text = "[agent]\ncommand = []\n"

    r = _run(text)

    assert r.applied == []
    assert any("command" in m and "empty" in m for m in r.manual)  # no auto-fix: real value needed


def test_empty_top_level_prompt_files_should_be_reported_manual():
    text = "[prompt]\nfiles = []\n"

    r = _run(text)

    assert r.applied == []
    assert any("[prompt] files" in m for m in r.manual)


def test_bare_prompt_arg_template_should_reach_manual_in_a_single_migrate_pass():
    """Fixpoint: a bare-string prompt_arg_template is wrapped to a list on pass 1,
    which the re-parse then reveals has no {prompt} placeholder — a single pass
    would exit clean on a config that still won't load_config. ONE run_migrations
    must reach the correctly-reported MANUAL, not the false clean."""
    text = '[agent]\ncommand = ["claude"]\nprompt_arg_template = "-p"\n'

    r = _run(text)

    assert any("prompt_arg_template" in a for a in r.applied)  # pass 1 wrapped it
    assert any("{prompt} placeholder" in m for m in r.manual)  # fixpoint caught it
    assert 'prompt_arg_template = ["-p"]' in r.new_text


def test_bare_prompt_arg_template_with_placeholder_should_be_loadable_after_one_migrate(tmp_path):
    """The converging case: a bare template that DOES carry {prompt} wraps to a
    genuinely-loadable list in one invocation — no lingering manual blocker."""
    from agent_runner.config import load_config

    prompt = tmp_path / "main.md"
    prompt.write_text("hi")
    text = (
        '[agent]\ncommand = ["claude"]\nprompt_arg_template = "{prompt}"\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path / "logs"}"\n'
        f'[prompt]\nfile = "{prompt}"\n'
    )

    r = _run(text)

    assert any("prompt_arg_template" in a for a in r.applied)
    assert r.manual == []
    cfg_path = tmp_path / "agent-runner.toml"
    cfg_path.write_text(r.new_text)
    load_config(cfg_path)  # must not raise — genuinely loadable after ONE migrate


def test_bare_per_phase_command_and_prompt_files_should_be_wrapped_into_lists():
    text = (
        '[phases]\nlist = ["dev"]\n'
        '[phases.dev.agent]\ncommand = "claude"\n'
        '[phases.dev.prompt]\nfiles = "dev.md"\n'
    )

    r = _run(text)

    assert 'command = ["claude"]' in r.new_text
    assert 'files = ["dev.md"]' in r.new_text
    assert r.manual == []


def test_empty_per_phase_command_should_be_reported_manual():
    # A per-phase [phases.<name>.agent] command = [] merges onto the base
    # [agent] table and overrides it to empty, hitting the same "non-empty
    # list" rejection as a top-level empty command — needs its own entry since
    # detecting it requires walking [phases.<name>.agent] sub-tables.
    text = '[phases]\nlist = ["dev"]\n[phases.dev.agent]\ncommand = []\n'

    r = _run(text)

    assert r.applied == []
    assert any("phases.<name>.agent" in m and "empty" in m for m in r.manual)
    assert r.new_text == text


def test_bare_per_phase_command_should_be_wrapped_without_touching_sibling_space_command():
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


# --- 0.2.13 strictness completion: table-as-scalar, base-table unknown keys,
# [phases] scalar keys, per-phase prompt unknown keys, argv {prompt} placeholder.
# Every new config.py rejection needs a matching registry entry here so
# `agent-runner migrate` reports it instead of crashing or missing it — the
# "migrate parity" contract. ---


@pytest.mark.parametrize(
    "table", ["agent", "runtime", "prompt", "vcs", "monitor", "phases", "plugins", "schedule"]
)
def test_scalar_table_value_should_be_reported_manual_without_crashing(table: str):
    # This is the registry-type-safety regression test: `monitor = 1` (and
    # every sibling top-level table given as a scalar) used to crash detect()
    # lambdas that assumed a dict (`p.get("monitor", {}).get(...)`). Detecting
    # and reporting it — never raising — is what lets `agent-runner migrate`
    # run at all on the exact config that needs it.
    text = f"{table} = 1\n"

    r = _run(text)  # must not raise

    assert r.applied == []
    assert any(table in m for m in r.manual)
    assert r.new_text == text


def test_monitor_scalar_value_should_not_crash_other_monitor_detectors():
    # A config with [monitor]-shaped content AND a scalar monitor= at once
    # can't happen in real TOML (one wins), but every OTHER detector that
    # reads "monitor" off the parsed dict must independently survive a scalar
    # monitor value, not just the anomaly-threshold compare.
    text = "monitor = 1\n"

    r = _run(text)  # must not raise from anomaly_repetitive_* or anywhere else

    assert any("monitor" in m for m in r.manual)


_UNKNOWN_KEY_MANUAL_CASES = [
    (
        "prompt",
        '[prompt]\nfile = "x.md"\nbogus = 1\n',
        lambda m: "bogus" in m and "[prompt]" in m,
        True,
    ),
    (
        "schedule",
        '[schedule]\ntimezone = "UTC"\nnope = 1\n',
        lambda m: "nope" in m and "[schedule]" in m,
        False,
    ),
    (
        "phase_schedule",
        '[phases]\nlist = ["dev"]\n[phases.dev.schedule]\nnope = 1\n',
        lambda m: "schedule" in m,
        False,
    ),
    (
        "agent",
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\nbogus = 1\n',
        lambda m: "bogus" in m and "[agent]" in m,
        False,
    ),
    (
        "runtime",
        '[runtime]\nwork_dir = "/x"\nlog_dir = "/x/logs"\nbogus = 1\n',
        lambda m: "bogus" in m and "[runtime]" in m,
        False,
    ),
    ("vcs", "[vcs]\nbogus = 1\n", lambda m: "bogus" in m and "[vcs]" in m, False),
    ("monitor", "[monitor]\nbogus = 1\n", lambda m: "bogus" in m and "[monitor]" in m, False),
    (
        "monitor_host_health",
        "[monitor.host_health]\nbogus = 1\n",
        lambda m: "bogus" in m and "[monitor.host_health]" in m,
        False,
    ),
    (
        "phase_prompt",
        '[phases]\nlist = ["dev"]\n[phases.dev.prompt]\nfiles = ["a.md"]\ninject_context = false\n',
        lambda m: "phases.<name>.prompt" in m,
        False,
    ),
]


@pytest.mark.parametrize(
    "text,check,check_new_text",
    [case[1:] for case in _UNKNOWN_KEY_MANUAL_CASES],
    ids=[case[0] for case in _UNKNOWN_KEY_MANUAL_CASES],
)
def test_unknown_key_should_be_reported_manual_when_present(text, check, check_new_text):
    r = _run(text)

    assert any(check(m) for m in r.manual)
    if check_new_text:
        assert r.new_text == text  # unknown-key rejections never rewrite the text


def test_phases_scalar_key_should_be_reported_manual():
    text = '[phases]\nlist = ["dev"]\nbogus = 1\n[phases.dev]\n'

    r = _run(text)

    assert any("bogus" in m for m in r.manual)


def test_agent_missing_prompt_placeholder_should_be_reported_manual():
    text = '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p"]\n'

    r = _run(text)

    assert any("{prompt}" in m for m in r.manual)


def test_agent_stdin_delivery_should_not_be_flagged_for_missing_prompt_placeholder():
    # stdin delivery legitimately has no {prompt} token in argv — a different,
    # already-enforced rule (config.py rejects the opposite: {prompt} present
    # under stdin). The migrate-side detector must not conflate the two.
    text = '[agent]\ncommand = ["true"]\nprompt_delivery = "stdin"\nprompt_arg_template = ["-p"]\n'

    r = _run(text)

    assert r.manual == []


def test_phase_agent_missing_prompt_placeholder_should_be_reported_manual():
    text = '[phases]\nlist = ["dev"]\n[phases.dev.agent]\nprompt_arg_template = ["-p"]\n'

    r = _run(text)

    assert any("{prompt}" in m for m in r.manual)


def test_phase_agent_missing_prompt_placeholder_should_not_be_flagged_when_files_is_empty():
    # Carve-out mirror of config.py's: a phase that disables its own prompt
    # (files = []) legitimately needs no {prompt} token in its own template.
    text = (
        '[phases]\nlist = ["dev"]\n'
        "[phases.dev.prompt]\nfiles = []\n"
        '[phases.dev.agent]\nprompt_arg_template = ["-p"]\n'
    )

    r = _run(text)

    assert r.manual == []
