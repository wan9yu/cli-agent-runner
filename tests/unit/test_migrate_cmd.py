import argparse
from pathlib import Path

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
