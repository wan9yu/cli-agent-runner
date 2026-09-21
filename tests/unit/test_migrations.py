import tomllib

import pytest

from agent_runner import migrations


def _run(text: str) -> migrations.MigrationResult:
    return migrations.run_migrations(text, tomllib.loads(text))


# migrate's terminal step always stamps schema_version = 1 onto the config
# (see test_migrate_should_stamp_schema_version_one_when_absent below) — every
# other test in this file exercises a config that has no schema_version yet
# (that's the whole point: these are pre-0.3 configs being migrated), so the
# stamp shows up in `applied` and at the front of `new_text` for all of them.
_STAMP_APPLIED = "stamped schema_version = 1"


def _stamped(text: str) -> str:
    return f"schema_version = 1\n{text}"


def test_rate_limit_action_rename_should_preserve_value_and_comment_when_invoked():
    text = '[runtime]\nrate_limit_action = "stop"   # keep on quota\n'

    r = _run(text)

    assert 'transient_error_action = "stop"   # keep on quota' in r.new_text
    assert "rate_limit_action" not in r.new_text
    assert r.applied == [
        "runtime.rate_limit_action → runtime.transient_error_action",
        _STAMP_APPLIED,
    ]
    assert r.manual == []


def test_orphan_action_rename_should_replace_key_with_dirty_action_when_invoked():
    text = '[vcs]\norphan_action = "ignore"\n'

    r = _run(text)

    assert 'dirty_action = "ignore"' in r.new_text
    assert "orphan_action" not in r.new_text
    assert r.applied == ["vcs.orphan_action → vcs.dirty_action", _STAMP_APPLIED]


def test_commented_out_key_should_stay_untouched_when_live_key_is_renamed():
    # A live key AND a commented-out one: the live line is renamed, the comment
    # is left byte-identical (the regex is anchored to real assignments).
    text = '[runtime]\n# rate_limit_action = "old"  historical note\nrate_limit_action = "stop"\n'

    r = _run(text)

    assert r.applied == [
        "runtime.rate_limit_action → runtime.transient_error_action",
        _STAMP_APPLIED,
    ]
    assert '# rate_limit_action = "old"  historical note' in r.new_text  # comment untouched
    assert 'transient_error_action = "stop"' in r.new_text


def test_round_timeout_per_phase_should_be_routed_to_manual_when_given_as_inline_table():
    text = "[runtime]\nround_timeout_per_phase = { dev = 900 }\n"

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert len(r.manual) == 1 and "round_timeout_per_phase" in r.manual[0]
    assert r.new_text == _stamped(text)  # manual transforms never touch the text itself


def test_orphan_action_rename_should_be_rejected_when_dirty_action_already_present():
    # Both the deprecated key AND the target key are set: a blind rename would
    # produce two `dirty_action` lines (invalid TOML). The rewrite must be
    # rejected and routed to manual, leaving the text untouched.
    text = '[vcs]\norphan_action = "ignore"\ndirty_action = "stash"\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert len(r.manual) == 1 and "remove the deprecated" in r.manual[0]
    assert r.new_text == _stamped(text)  # invalid rewrite was not adopted


def test_up_to_date_config_should_be_a_noop_when_invoked():
    text = '[runtime]\ntransient_error_action = "back_off"\n[vcs]\ndirty_action = "stash"\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED] and r.manual == [] and r.new_text == _stamped(text)


def test_flat_phase_override_should_be_advisory_when_set_directly():
    # A flat override directly under [phases.a] is guidance, not a rejection
    # — the flat form is a PERMANENT alias (config.py still loads it).
    # Routing it through `manual` used to make `upgrade` refuse to cross the
    # version boundary forever over a config that was never broken; it must
    # report as advisory and never block applied/manual-gated callers.
    # (disable_pre_round_hooks used to be a second parametrized case here —
    # it's no longer a valid alias at all, see the drop-migration tests below.)
    text = 'phases.list = ["a"]\n[phases.a]\nround_budget_s = 900\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert r.manual == []
    assert len(r.advisory) == 1 and "[phases.<name>.runtime]" in r.advisory[0]
    assert r.new_text == _stamped(text)  # advisory transforms never touch the text itself


def test_nested_phase_runtime_table_should_not_be_flagged_when_invoked():
    text = 'phases.list = ["a"]\n[phases.a.runtime]\nround_budget_s = 900\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED] and r.manual == [] and r.new_text == _stamped(text)


def test_rate_limit_action_rename_should_be_table_scoped_when_key_is_in_other_tables():
    # A plugin sub-table with the same key name must NOT be renamed — only the
    # real [runtime] one. (Regression: the pre-0.2.12 rename was table-blind.)
    text = '[runtime]\nrate_limit_action = "stop"\n[plugins.foo]\nrate_limit_action = "keep"\n'

    r = _run(text)

    assert 'transient_error_action = "stop"' in r.new_text
    assert '[plugins.foo]\nrate_limit_action = "keep"' in r.new_text  # untouched
    assert r.applied == [
        "runtime.rate_limit_action → runtime.transient_error_action",
        _STAMP_APPLIED,
    ]


def test_bare_single_token_command_should_be_wrapped_into_argv_list_when_invoked():
    text = '[agent]\ncommand = "claude"   # main agent\n'

    r = _run(text)

    assert 'command = ["claude"]   # main agent' in r.new_text
    assert r.manual == []
    assert any("command" in a for a in r.applied)


def test_command_with_spaces_should_be_reported_manual_not_auto_split_when_invoked():
    text = '[agent]\ncommand = "claude -p"\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert any("argv list" in m for m in r.manual)
    assert r.new_text == _stamped(text)  # never auto-split quoted argv


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


def test_empty_command_should_be_reported_manual_when_invoked():
    text = "[agent]\ncommand = []\n"

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert any("command" in m and "empty" in m for m in r.manual)  # no auto-fix: real value needed


def test_empty_top_level_prompt_files_should_be_reported_manual_when_invoked():
    text = "[prompt]\nfiles = []\n"

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert any("[prompt] files" in m for m in r.manual)


def test_bare_prompt_arg_template_should_reach_manual_in_a_single_migrate_pass_when_invoked():
    """Fixpoint: a bare-string prompt_arg_template is wrapped to a list on pass 1,
    which the re-parse then reveals has no {prompt} placeholder — a single pass
    would exit clean on a config that still won't load_config. ONE run_migrations
    must reach the correctly-reported MANUAL, not the false clean."""
    text = '[agent]\ncommand = ["claude"]\nprompt_arg_template = "-p"\n'

    r = _run(text)

    assert any("prompt_arg_template" in a for a in r.applied)  # pass 1 wrapped it
    assert any("{prompt} placeholder" in m for m in r.manual)  # fixpoint caught it
    assert 'prompt_arg_template = ["-p"]' in r.new_text


def test_bare_prompt_arg_template_with_placeholder_should_be_loadable_after_one_migrate_when_run(
    tmp_path,
):
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


def test_bare_per_phase_command_and_prompt_files_should_be_wrapped_into_lists_when_invoked():
    text = (
        '[phases]\nlist = ["dev"]\n'
        '[phases.dev.agent]\ncommand = "claude"\n'
        '[phases.dev.prompt]\nfiles = "dev.md"\n'
    )

    r = _run(text)

    assert 'command = ["claude"]' in r.new_text
    assert 'files = ["dev.md"]' in r.new_text
    assert r.manual == []


def test_empty_per_phase_command_should_be_reported_manual_when_invoked():
    # A per-phase [phases.<name>.agent] command = [] merges onto the base
    # [agent] table and overrides it to empty, hitting the same "non-empty
    # list" rejection as a top-level empty command — needs its own entry since
    # detecting it requires walking [phases.<name>.agent] sub-tables.
    text = '[phases]\nlist = ["dev"]\n[phases.dev.agent]\ncommand = []\n'

    r = _run(text)

    assert r.applied == [_STAMP_APPLIED]
    assert any("phases.<name>.agent" in m and "empty" in m for m in r.manual)
    assert r.new_text == _stamped(text)


def test_bare_per_phase_command_should_be_wrapped_without_touching_sibling_space_command_when_run():
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
def test_scalar_table_value_should_be_reported_manual_without_crashing_when_invoked(table: str):
    # This is the registry-type-safety regression test: `monitor = 1` (and
    # every sibling top-level table given as a scalar) used to crash detect()
    # lambdas that assumed a dict (`p.get("monitor", {}).get(...)`). Detecting
    # and reporting it — never raising — is what lets `agent-runner migrate`
    # run at all on the exact config that needs it.
    text = f"{table} = 1\n"

    r = _run(text)  # must not raise

    assert r.applied == [_STAMP_APPLIED]
    assert any(table in m for m in r.manual)
    assert r.new_text == _stamped(text)


def test_monitor_scalar_value_should_not_crash_other_monitor_detectors_when_invoked():
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
        assert r.new_text == _stamped(text)  # unknown-key rejections never rewrite the text itself


def test_phases_scalar_key_should_be_reported_manual_when_invoked():
    text = '[phases]\nlist = ["dev"]\nbogus = 1\n[phases.dev]\n'

    r = _run(text)

    assert any("bogus" in m for m in r.manual)


def test_agent_missing_prompt_placeholder_should_be_reported_manual_when_invoked():
    text = '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p"]\n'

    r = _run(text)

    assert any("{prompt}" in m for m in r.manual)


def test_agent_stdin_delivery_should_not_be_flagged_for_missing_prompt_placeholder_when_invoked():
    # stdin delivery legitimately has no {prompt} token in argv — a different,
    # already-enforced rule (config.py rejects the opposite: {prompt} present
    # under stdin). The migrate-side detector must not conflate the two.
    text = '[agent]\ncommand = ["true"]\nprompt_delivery = "stdin"\nprompt_arg_template = ["-p"]\n'

    r = _run(text)

    assert r.manual == []


def test_phase_agent_missing_prompt_placeholder_should_be_reported_manual_when_invoked():
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


def test_swap_sout_noise_floor_mb_should_relocate_under_memory_subtable_when_migrated():
    text = "[monitor.host_health]\nswap_sout_noise_floor_mb = 64\nmem_avail_min_mb = 100\n"

    r = _run(text)

    assert "[monitor.host_health.memory]" in r.new_text
    assert "swap_out_noise_floor_mb = 64" in r.new_text
    assert "avail_min_mb = 100" in r.new_text
    assert "swap_sout_noise_floor_mb" not in r.new_text
    assert "mem_avail_min_mb" not in r.new_text
    assert tomllib.loads(r.new_text)["monitor"]["host_health"]["memory"] == {
        "swap_out_noise_floor_mb": 64,
        "avail_min_mb": 100,
    }


def test_full_flat_host_health_table_should_migrate_to_all_three_subtables_when_run():
    text = (
        "[monitor.host_health]\n"
        "mem_avail_min_mb = 150\n"
        "disk_warning_pct = 85.0\n"
        "disk_critical_pct = 92.0\n"
        "swap_sout_noise_floor_mb = 40\n"
        "mem_free_low_mb = 20\n"
        "psi_full_avg10_critical = 55.0\n"
        "psi_some_avg10_warning = 4.0\n"
        "mem_critical_consecutive_samples = 2\n"
        "in_round_mem_terminate = false\n"
    )

    r = _run(text)

    parsed = tomllib.loads(r.new_text)
    assert set(parsed["monitor"]["host_health"]) == {"disk", "memory", "pressure"}
    assert parsed["monitor"]["host_health"]["disk"] == {"warning_pct": 85.0, "critical_pct": 92.0}
    assert parsed["monitor"]["host_health"]["memory"] == {
        "avail_min_mb": 150,
        "free_low_mb": 20,
        "swap_out_noise_floor_mb": 40,
    }
    assert parsed["monitor"]["host_health"]["pressure"] == {
        "full_avg10_critical": 55.0,
        "some_avg10_warning": 4.0,
        "critical_consecutive_samples": 2,
        "in_round_terminate": False,
    }
    assert r.manual == []


def test_round_timeout_s_should_rename_in_runtime_table_when_migrated():
    text = "[runtime]\nround_timeout_s = 3600\n"

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert "round_budget_s = 3600" in r.new_text
    assert r.applied == ["runtime.round_timeout_s → runtime.round_budget_s", _STAMP_APPLIED]


def test_round_timeout_s_should_rename_in_every_phase_flat_override_when_migrated():
    text = (
        '[phases]\nlist = ["a", "b"]\n'
        "[phases.a]\nround_timeout_s = 100\n"
        "[phases.b]\nround_timeout_s = 200\n"
    )

    r = migrations.run_migrations(text, tomllib.loads(text))

    parsed = tomllib.loads(r.new_text)
    assert parsed["phases"]["a"]["round_budget_s"] == 100
    assert parsed["phases"]["b"]["round_budget_s"] == 200


def test_round_timeout_s_should_rename_in_nested_phase_runtime_table_when_migrated():
    text = '[phases]\nlist = ["a"]\n[phases.a.runtime]\nround_timeout_s = 300\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert tomllib.loads(r.new_text)["phases"]["a"]["runtime"]["round_budget_s"] == 300


def test_disable_pre_round_hooks_should_be_dropped_from_runtime_table_when_migrated():
    # 0.3.9: disable_pre_round_hooks's only consumer (the PreRoundHook plugin
    # seam) was removed — the key has no replacement, so migrate DROPS it
    # (contrast the round_timeout_s renames above, which keep the value).
    text = "[runtime]\nround_budget_s = 900\ndisable_pre_round_hooks = true\n"

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert "disable_pre_round_hooks" not in r.new_text
    assert "round_budget_s = 900" in r.new_text  # sibling key untouched
    assert any("runtime.disable_pre_round_hooks" in a for a in r.applied)
    tomllib.loads(r.new_text)  # still valid TOML


def test_disable_pre_round_hooks_should_be_dropped_from_every_phase_flat_override_when_migrated():
    text = (
        '[phases]\nlist = ["a", "b"]\n'
        "[phases.a]\ndisable_pre_round_hooks = true\n"
        "[phases.b]\ndisable_pre_round_hooks = false\nround_budget_s = 200\n"
    )

    r = migrations.run_migrations(text, tomllib.loads(text))

    parsed = tomllib.loads(r.new_text)
    assert "disable_pre_round_hooks" not in parsed["phases"]["a"]
    assert "disable_pre_round_hooks" not in parsed["phases"]["b"]
    assert parsed["phases"]["b"]["round_budget_s"] == 200  # sibling key untouched


def test_disable_pre_round_hooks_should_be_dropped_from_nested_phase_runtime_table_when_migrated():
    text = '[phases]\nlist = ["a"]\n[phases.a.runtime]\ndisable_pre_round_hooks = true\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    parsed = tomllib.loads(r.new_text)
    assert "disable_pre_round_hooks" not in parsed["phases"]["a"].get("runtime", {})


def test_old_hook_level_disable_name_should_be_flagged_manual_when_migrated():
    text = '[plugins]\ndisable = ["claude_error_detector"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    # The old hook name is flagged and the mapping points at the CURRENT plugin
    # name (claude, not the intermediate claude_rate_limit).
    assert any("claude_error_detector" in m and "'claude'" in m for m in r.manual)


def test_renamed_claude_plugin_disable_name_should_be_flagged_manual_when_migrated():
    """0.3.9 rename: `disable = ["claude_rate_limit"]` must be flagged (never left
    silently ineffective) with the new `claude` name."""
    text = '[plugins]\ndisable = ["claude_rate_limit"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert any("claude_rate_limit" in m and "'claude'" in m for m in r.manual)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ('[plugins]\nsandbox = "prefer"\n', "plugins.sandbox"),
        ('[plugins]\npin = { my_plugin = "sha256:abc" }\n', "plugins.pin"),
        ('[plugins]\nspawn_override_allow = ["my_hook"]\n', "plugins.spawn_override_allow"),
    ],
    ids=["sandbox", "pin", "spawn_override_allow"],
)
def test_removed_plugins_sandbox_key_should_be_flagged_manual_when_migrated(text, needle):
    """0.3.9 dropped the third-party sandbox trampoline's three [plugins] keys
    with no replacement -- detect() must actually FIRE on each (not just be
    described in the registry) and route to `manual`, never `applied`."""
    r = migrations.run_migrations(text, tomllib.loads(text))

    assert any(needle in m and "removed 0.3.9" in m for m in r.manual)
    assert r.new_text == _stamped(text)  # manual-only: the key is left in place


def test_removed_plugins_raw_leftover_key_should_be_flagged_manual_when_migrated():
    """0.3.9 dropped [plugins]'s `.raw` forward-compat catch-all: any key that
    used to be silently absorbed into it (anything other than `disable` and the
    three sandbox keys above, each covered by their own dedicated case) is now
    an unknown key -- the generic [plugins] unknown-key detector must fire."""
    text = '[plugins]\nmy_plugin_setting = "value"\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert any("my_plugin_setting" in m and "[plugins]" in m and "0.3.9" in m for m in r.manual)


def test_removed_plugins_sandbox_key_should_not_double_report_via_generic_detector_when_invoked():
    """sandbox/pin/spawn_override_allow are excluded from the generic [plugins]
    unknown-key check (_PLUGINS_LEGACY_FIELDS) so a config using only one of
    them is reported exactly once, by its own dedicated Migration."""
    text = '[plugins]\nsandbox = "prefer"\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert sum("sandbox" in m for m in r.manual) == 1


def test_migrate_should_stamp_schema_version_one_when_absent():
    text = '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert r.new_text.splitlines()[0] == "schema_version = 1"
    assert tomllib.loads(r.new_text)["schema_version"] == 1


def test_migrate_should_be_a_no_op_when_schema_version_already_one():
    text = 'schema_version = 1\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert r.new_text == text
    assert r.applied == []


def test_migrate_should_replace_string_schema_version_with_int_one_when_non_canonical():
    # A pre-existing but non-canonical schema_version (here a string "1", not
    # the int 1) must be REPLACED in place, never prepended — prepending would
    # leave two top-level `schema_version` keys, which is invalid TOML and
    # would corrupt the config on its next load despite `migrate` reporting
    # success.
    text = 'schema_version = "1"\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    parsed = tomllib.loads(r.new_text)  # raises on a duplicate top-level key
    assert parsed["schema_version"] == 1
    assert type(parsed["schema_version"]) is int
    assert r.new_text.count("schema_version") == 1


def test_migrate_should_replace_bool_schema_version_with_int_one_when_true():
    # `True == 1` in Python, so a naive `== _CURRENT_SCHEMA_VERSION` check
    # would wrongly treat `schema_version = true` as already-current and skip
    # normalizing it to the real int.
    text = (
        'schema_version = true\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
    )

    r = migrations.run_migrations(text, tomllib.loads(text))

    parsed = tomllib.loads(r.new_text)
    assert parsed["schema_version"] == 1
    assert type(parsed["schema_version"]) is int
    assert r.new_text.count("schema_version") == 1


def test_migrate_should_leave_newer_schema_version_unchanged_when_already_ahead():
    # A config already stamped by a NEWER agent-runner must not be downgraded,
    # and must not gain a duplicate top-level `schema_version` key either.
    text = 'schema_version = 2\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'

    r = migrations.run_migrations(text, tomllib.loads(text))

    assert r.new_text == text
    assert r.applied == []
    assert r.new_text.count("schema_version") == 1
