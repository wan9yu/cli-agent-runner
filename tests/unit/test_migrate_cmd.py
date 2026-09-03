import argparse
from pathlib import Path

import pytest

from agent_runner.cli import migrate_cmd


def _args(cfg: Path, dry_run: bool = False):
    return argparse.Namespace(config=str(cfg), dry_run=dry_run, json=False)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_migrate_writes_and_backs_up(tmp_path, capsys):
    original = '[runtime]\nrate_limit_action = "stop"\n'
    cfg = _write(tmp_path, original)
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 0
    assert 'transient_error_action = "stop"' in cfg.read_text()
    assert (tmp_path / "agent-runner.toml.bak").read_text() == original


def test_migrate_symlinked_config_backs_up_beside_symlink_not_target(tmp_path):
    """cfg_path routes through _resolve.config_path (.absolute(), never
    .resolve()) -- a symlinked --config gets its .bak sibling next to the
    symlink, not silently relocated to the symlink's target directory."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    original = '[runtime]\nrate_limit_action = "stop"\n'
    real_cfg = real_dir / "agent-runner.toml"
    real_cfg.write_text(original)

    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link_cfg = link_dir / "agent-runner.toml"
    link_cfg.symlink_to(real_cfg)

    rc = migrate_cmd.cmd(_args(link_cfg))

    assert rc == 0
    assert (link_dir / "agent-runner.toml.bak").exists()
    assert not (real_dir / "agent-runner.toml.bak").exists()


def test_dry_run_writes_nothing(tmp_path, capsys):
    original = '[runtime]\nrate_limit_action = "stop"\n'
    cfg = _write(tmp_path, original)
    rc = migrate_cmd.cmd(_args(cfg, dry_run=True))
    assert rc == 0
    assert cfg.read_text() == original  # unchanged
    assert not (tmp_path / "agent-runner.toml.bak").exists()
    assert "dry-run" in capsys.readouterr().out.lower()


def test_nothing_to_migrate_is_noop(tmp_path):
    cfg = _write(tmp_path, '[runtime]\ntransient_error_action = "back_off"\n')
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 0
    assert not (tmp_path / "agent-runner.toml.bak").exists()


def test_manual_transform_exits_1(tmp_path):
    cfg = _write(tmp_path, "[runtime]\nround_timeout_per_phase = { dev = 900 }\n")
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1  # human action still needed


def test_duplicate_key_rename_exits_1_and_leaves_file(tmp_path):
    # Both the deprecated and target keys present: the rename would duplicate a
    # key, so it must NOT be applied — migrate reports the manual step (exit 1)
    # and writes nothing (no rewrite, no .bak).
    original = '[vcs]\norphan_action = "ignore"\ndirty_action = "stash"\n'
    cfg = _write(tmp_path, original)
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1
    assert cfg.read_text() == original  # untouched
    assert not (tmp_path / "agent-runner.toml.bak").exists()


def test_broken_toml_exits_2(tmp_path):
    cfg = _write(tmp_path, "[runtime\n not toml")
    assert migrate_cmd.cmd(_args(cfg)) == 2


def test_integration_migrated_config_loads(tmp_path):
    from agent_runner.config import load_config

    cfg = _write(
        tmp_path,
        (
            '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
            "[runtime]\n"
            f'work_dir = "{tmp_path}"\n'
            f'log_dir = "{tmp_path / "logs"}"\n'
            'rate_limit_action = "skip"\n'
            '[vcs]\norphan_action = "ignore"\n'
            '[prompt]\nfile = "p.md"\n'
        ),
    )
    (tmp_path / "p.md").write_text("hi")
    assert migrate_cmd.cmd(_args(cfg)) == 0
    loaded = load_config(cfg)  # was rejecting the old keys before
    assert loaded.runtime.transient_error_action == "skip"
    assert loaded.vcs.dirty_action == "ignore"


def test_migrate_bare_command_then_config_loads(tmp_path):
    from agent_runner.config import load_config

    body = (
        '[agent]\ncommand = "true"\nprompt_arg_template = ["-p", "{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
    )
    cfg = _write(tmp_path, body)
    assert migrate_cmd.cmd(_args(cfg)) == 0
    load_config(cfg)  # the rewritten file loads clean under 0.2.12 strictness


def test_flat_phase_alias_is_advisory_not_blocking(tmp_path, capsys):
    # The flat [phases.<name>] round_timeout_s/disable_pre_round_hooks alias is
    # a PERMANENT, still-valid form — reporting it as `manual` (exit 1 forever)
    # was the bug: `migrate` must surface it as guidance without blocking.
    cfg = _write(tmp_path, 'phases.list = ["a"]\n[phases.a]\nround_timeout_s = 900\n')
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 0
    assert "[phases.<name>.runtime]" in capsys.readouterr().out


def test_unknown_runtime_key_round_trip(tmp_path):
    """A brand-new 0.2.13 rejection (unknown [runtime] key): load_config
    raises, migrate reports it (exit 1, guided — not a crash), and deleting
    the stray key by hand makes the config load clean again."""
    from agent_runner.config import ConfigError, load_config

    body = (
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\nbogus = 1\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
    )
    cfg = _write(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    with pytest.raises(ConfigError):
        load_config(cfg)

    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1  # manual: guided, not a crash

    cfg.write_text(cfg.read_text().replace("bogus = 1\n", ""))
    load_config(cfg)  # now loads clean


def test_table_as_scalar_round_trip(tmp_path):
    """Table-as-scalar ([monitor] given as `monitor = 1`): load_config
    raises, migrate reports it without crashing, and giving it real table
    content makes the config load clean again."""
    from agent_runner.config import ConfigError, load_config

    body = (
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "monitor = 1\n"
    )
    cfg = _write(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    with pytest.raises(ConfigError):
        load_config(cfg)

    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1  # manual: guided, not a crash

    cfg.write_text(cfg.read_text().replace("monitor = 1\n", ""))
    load_config(cfg)  # now loads clean


def test_monitor_host_health_unknown_key_round_trip(tmp_path):
    """0.2.14 (BREAKING): [monitor.host_health] unknown key: load_config
    raises, migrate reports it (exit 1, guided — not a crash), and deleting
    the stray key by hand makes the config load clean again."""
    from agent_runner.config import ConfigError, load_config

    body = (
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        "[monitor.host_health]\nbogus = 1\n"
    )
    cfg = _write(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    with pytest.raises(ConfigError):
        load_config(cfg)

    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1  # manual: guided, not a crash

    cfg.write_text(cfg.read_text().replace("bogus = 1\n", ""))
    load_config(cfg)  # now loads clean


def test_argv_missing_placeholder_round_trip(tmp_path):
    """argv prompt_arg_template missing {prompt}: load_config raises, migrate
    reports it (no safe auto-fix — the insertion point is a real decision),
    and adding the token by hand makes the config load clean again."""
    from agent_runner.config import ConfigError, load_config

    body = (
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
    )
    cfg = _write(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    with pytest.raises(ConfigError):
        load_config(cfg)

    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 1  # manual: guided, not a crash

    fixed = cfg.read_text().replace(
        'prompt_arg_template = ["-p"]', 'prompt_arg_template = ["-p", "{prompt}"]'
    )
    cfg.write_text(fixed)
    load_config(cfg)  # now loads clean
