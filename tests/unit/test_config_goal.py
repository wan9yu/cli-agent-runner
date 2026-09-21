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


# --- [goal] table: objective goal-checks + lessons-ledger ---

_MIN_AGENT_RUNTIME = """\
[agent]
command = ["true"]
prompt_arg_template = ["{{prompt}}"]
[runtime]
work_dir = "{tmp_path}"
log_dir = "{tmp_path}/logs"
"""


def test_goal_should_be_none_when_table_absent(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path) + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is None


def test_goal_checks_should_parse_when_loaded(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + "[goal]\n"
        'ledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest", "-q"]\n'
        'cwd = "sub"\n'
        "timeout_s = 20\n"
        "[[goal.checks]]\n"
        'name = "lint"\n'
        'cmd = ["ruff", "check", "."]\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.ledger == str(tmp_path / "logs" / "ledger.md")
    assert len(cfg.goal.checks) == 2
    first, second = cfg.goal.checks
    assert first.name == "tests"
    assert first.cmd == ["pytest", "-q"]
    assert first.cwd == "sub"
    assert first.timeout_s == 20
    assert second.name == "lint"
    assert second.cmd == ["ruff", "check", "."]
    assert second.cwd is None
    assert second.timeout_s == 10  # default


def test_goal_checks_allowance_s_should_sum_check_timeouts_plus_kill_grace_per_check_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + "[goal]\n"
        'ledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest", "-q"]\n'
        "timeout_s = 20\n"
        "[[goal.checks]]\n"
        'name = "lint"\n'
        'cmd = ["ruff", "check", "."]\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    # timeout_s: 20 (explicit) + 10 (default) = 30, plus TWO kill graces PER
    # check (checks run sequentially, no short-circuit -- each breach costs a
    # TERM->killpg grace AND a bounded post-kill drain grace): 2 checks * 2 *
    # _bounded._KILL_GRACE_S(3) = 12.
    assert cfg.goal.checks_allowance_s == 42


def test_goal_checks_allowance_s_should_be_zero_when_no_checks(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.md").write_text("a")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["a.md", "logs/ledger.md"]\n'
        + '[goal]\nledger = "logs/ledger.md"\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.checks_allowance_s == 0


def test_goal_unknown_field_should_raise_config_error_when_loaded(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "ledger.md"\nbogus = true\n',
    )

    with pytest.raises(ValueError, match=r"unknown \[goal\] field"):
        load_config(toml)


def test_goal_check_unknown_field_should_raise_config_error_when_loaded(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest"]\n'
        "bogus = 1\n",
    )

    with pytest.raises(ValueError, match=r"unknown \[goal\.checks\.0\] field"):
        load_config(toml)


def test_goal_check_timeout_over_cap_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest"]\n'
        "timeout_s = 999\n",
    )

    with pytest.raises(ValueError, match=r"goal\.checks\.0\.timeout_s.*<= 30"):
        load_config(toml)


def test_goal_check_timeout_at_cap_should_load_successfully_when_invoked(tmp_path: Path) -> None:
    """Cap boundary: timeout_s == _MAX_GOAL_CHECK_TIMEOUT_S (30) is accepted."""
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + '[goal]\nledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest"]\n'
        "timeout_s = 30\n",
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.checks[0].timeout_s == 30


def test_goal_check_timeout_one_over_cap_should_raise_config_error_when_invoked(
    tmp_path: Path,
) -> None:
    """Cap boundary: timeout_s == 31 is rejected -- and with ConfigError, the
    config-load error type, not a bare ValueError."""
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "tests"\n'
        'cmd = ["pytest"]\n'
        "timeout_s = 31\n",
    )

    with pytest.raises(ConfigError, match=r"goal\.checks\.0\.timeout_s.*<= 30"):
        load_config(toml)


def test_goal_ledger_inside_work_dir_outside_log_dir_should_be_rejected_when_invoked(
    tmp_path: Path,
) -> None:
    """Stash-swept-ledger boot guard: a ledger resolved inside work_dir but
    outside log_dir is
    swept by the default dirty_action="stash" `git stash push -u`, so the steer
    would last one round. Reject it at load with a message that names the fix.
    (Mutation check: dropping the guard makes this load succeed and the test
    fail.)"""
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        # ledger.md sits at work_dir root -- inside work_dir, outside logs/.
        + '[prompt]\nfiles = ["prompt.md", "ledger.md"]\n'
        + '[goal]\nledger = "ledger.md"\n',
    )

    with pytest.raises(ConfigError, match=r"inside runtime\.work_dir.*outside runtime\.log_dir"):
        load_config(toml)


def test_goal_ledger_at_absolute_path_outside_work_dir_should_load_successfully_when_invoked(
    tmp_path: Path,
) -> None:
    """The stash-swept-ledger boot guard must NOT false-reject a legitimate
    ledger at an absolute
    path outside work_dir (it is never in the git tree, so the stash can't sweep
    it)."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "prompt.md").write_text("p")
    outside_ledger = tmp_path / "state" / "lessons.md"
    body = (
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work}"\n'
        f'log_dir = "{work}/logs"\n'
        "[prompt]\n"
        f'files = ["prompt.md", "{outside_ledger}"]\n'
        "[goal]\n"
        f'ledger = "{outside_ledger}"\n'
    )
    toml = _write_toml(tmp_path, body)

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.ledger == str(outside_ledger)


def test_goal_checks_budget_at_else_above_fast_spin_window_should_warn_when_invoked(
    tmp_path: Path,
) -> None:
    """A goal-check budget >= the fast-spin give-up window emits a load-time
    WARNING (not a hard reject -- a legitimate pytest check may exceed it). One
    check at the 30s cap -> allowance 30 + 2*3 = 36 >= 30. (Mutation check:
    dropping the warn makes this raise for no-warning-emitted and the test
    fails.)"""
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + '[goal]\nledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "slow"\n'
        'cmd = ["pytest"]\n'
        "timeout_s = 30\n",
    )

    with pytest.warns(UserWarning, match=r"fast-spin give-up window"):
        cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.checks_allowance_s == 36


def test_goal_checks_budget_below_fast_spin_window_should_not_warn_when_invoked(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    """A modest goal-check budget below the fast-spin window emits no such
    warning -- the disarm-risk warning is edge-only, not noise on every goal
    config."""
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + '[goal]\nledger = "logs/ledger.md"\n'
        "[[goal.checks]]\n"
        'name = "fast"\n'
        'cmd = ["true"]\n'
        "timeout_s = 10\n",  # allowance 10 + 2*3 = 16 < 30
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.checks_allowance_s == 16
    assert not [w for w in recwarn.list if "fast-spin give-up window" in str(w.message)]


def test_goal_scalar_checks_should_be_rejected_when_not_a_list(tmp_path: Path) -> None:
    """goal.checks must be a list of tables, not a scalar -- the bare-scalar
    footgun _require_str_list guards elsewhere."""
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "ledger.md"\nchecks = "nope"\n',
    )

    with pytest.raises(ValueError, match=r"goal\.checks.*list of tables"):
        load_config(toml)


def test_goal_ledger_missing_from_prompt_files_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md"]\n'
        + '[goal]\nledger = "ledger.md"\n',
    )

    with pytest.raises(ValueError, match=r"\[prompt\]: \[goal\] ledger .*index >= 1"):
        load_config(toml)


def test_goal_ledger_at_index_zero_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["ledger.md", "prompt.md"]\n'
        + '[goal]\nledger = "ledger.md"\n',
    )

    with pytest.raises(ValueError, match=r"\[prompt\]: \[goal\] ledger .*index >= 1"):
        load_config(toml)


def test_goal_with_single_prompt_file_form_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + f'[prompt]\nfile = "{tmp_path}/prompt.md"\n'
        + '[goal]\nledger = "ledger.md"\n',
    )

    with pytest.raises(ValueError, match=r"\[prompt\]: \[goal\] ledger .*index >= 1"):
        load_config(toml)


def test_goal_ledger_at_index_one_should_load_successfully_when_invoked(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("p")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "logs/ledger.md"]\n'
        + '[goal]\nledger = "logs/ledger.md"\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.goal.ledger == str(tmp_path / "logs" / "ledger.md")


def test_goal_with_phase_empty_prompt_files_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A phase whose own prompt.files = [] sends no prompt at all -- under
    [goal] that's still an error, since the ledger can never ride along."""
    (tmp_path / "prompt.md").write_text("p")

    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["prompt.md", "ledger.md"]\n'
        + '[phases]\nlist = ["dev"]\n'
        "[phases.dev.prompt]\nfiles = []\n" + '[goal]\nledger = "ledger.md"\n',
    )

    with pytest.raises(ValueError, match=r"\[phases\.dev\.prompt\]: \[goal\] ledger .*index >= 1"):
        load_config(toml)


def test_goal_with_phase_override_ledger_at_index_one_should_load_successfully_when_invoked(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.md").write_text("a")
    toml = _write_toml(
        tmp_path,
        _MIN_AGENT_RUNTIME.format(tmp_path=tmp_path)
        + '[prompt]\nfiles = ["a.md", "logs/ledger.md"]\n'
        + '[phases]\nlist = ["dev"]\n'
        "[phases.dev.prompt]\n"
        'files = ["a.md", "logs/ledger.md"]\n' + '[goal]\nledger = "logs/ledger.md"\n',
    )

    cfg = load_config(toml)

    assert cfg.goal is not None
    assert cfg.phases.overrides["dev"].prompt_files == [
        tmp_path / "a.md",
        tmp_path / "logs" / "ledger.md",
    ]
