"""Config.profile_for + per-phase agent/schedule/runtime sub-tables (0.2.9).

Behavior-neutral for existing configs: with no per-phase agent/schedule the
resolved profile returns base agent/runtime/schedule by identity.
"""

import pytest

from agent_runner.config import ConfigError, load_config
from tests._test_helpers import make_toml_with_sections


def _cfg(tmp_path, phases_block):
    p = make_toml_with_sections(tmp_path, phases_block=phases_block)  # appends after [prompt]
    return load_config(p)


def test_phase_agent_override_should_merge_onto_base(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[phases]\nlist = ["a","b"]\n[phases.b.agent]\ncommand = ["glm-cli"]\n',
    )

    prof_a = cfg.profile_for("a")
    prof_b = cfg.profile_for("b")

    assert prof_a.agent.command == cfg.agent.command  # inherits base
    assert prof_b.agent.command == ["glm-cli"]  # override
    assert prof_b.agent.prompt_arg_template == cfg.agent.prompt_arg_template  # unset field inherits


def test_phase_schedule_should_replace_global_windows_when_phase_sets_its_own(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\npause_windows = ["09:00-12:00"]\n'
        '[phases]\nlist = ["a","b"]\n'
        "[phases.b.schedule]\npause_windows = []\n",
    )

    assert cfg.profile_for("a").schedule.pause_windows != ()  # inherits global
    assert cfg.profile_for("b").schedule.pause_windows == ()  # replaced (empty)


def test_phase_schedule_should_inherit_global_timezone_when_phase_omits_it(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a"]\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    assert cfg.profile_for("a").schedule.timezone == "Asia/Shanghai"  # tz falls back to global


def test_flat_runtime_alias_should_set_round_timeout_s_on_profile(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a"]\n[phases.a]\nround_timeout_s = 3600\n')

    assert cfg.profile_for("a").runtime.round_timeout_s == 3600


def test_nested_runtime_sub_table_should_set_round_timeout_s_on_profile(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[phases]\nlist = ["a"]\n[phases.a.runtime]\nround_timeout_s = 900\n',
    )

    assert cfg.profile_for("a").runtime.round_timeout_s == 900


def test_flat_and_nested_runtime_twin_should_error(tmp_path):
    with pytest.raises(ConfigError, match="both"):
        _cfg(
            tmp_path,
            '[phases]\nlist = ["a"]\n'
            "[phases.a]\nround_timeout_s = 3600\n"
            "[phases.a.runtime]\nround_timeout_s = 900\n",
        )


def test_bad_phase_agent_should_fail_at_load(tmp_path):
    # stdin + {prompt} in argv template is the cross-check that must run on the MERGED agent
    with pytest.raises(ConfigError):
        _cfg(
            tmp_path,
            '[phases]\nlist = ["a"]\n'
            '[phases.a.agent]\nprompt_delivery = "stdin"\nprompt_arg_template = ["{prompt}"]\n',
        )


def test_unknown_phase_agent_field_should_error(tmp_path):
    with pytest.raises(ConfigError, match="made_up"):
        _cfg(
            tmp_path,
            '[phases]\nlist = ["a"]\n[phases.a.agent]\nmade_up = 1\n',
        )


def test_phase_policy_should_default_to_wait(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a"]\n')

    assert cfg.phases.phase_policy == "wait"


def test_phase_policy_should_parse_skip_value(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n')

    assert cfg.phases.phase_policy == "skip"


def test_phase_policy_should_error_when_value_is_invalid(tmp_path):
    with pytest.raises(ConfigError, match="phase_policy"):
        _cfg(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "nope"\n')


def test_profile_for_should_return_base_identity_when_no_phases_configured(tmp_path):
    cfg = _cfg(tmp_path, "")

    prof = cfg.profile_for(None)

    assert prof.agent is cfg.agent
    assert prof.runtime is cfg.runtime
    assert prof.schedule is cfg.schedule
    assert prof.prompt_files is None


def test_profile_for_should_return_base_identity_when_phase_name_is_unknown(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a"]\n')

    prof = cfg.profile_for("nope")  # defensive: config-load catches typos

    assert prof.runtime is cfg.runtime
    assert prof.agent is cfg.agent
