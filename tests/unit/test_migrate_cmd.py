import argparse
from pathlib import Path

import pytest

from agent_runner.cli import migrate_cmd
from agent_runner.config import ConfigError, load_config


def _args(cfg: Path, dry_run: bool = False):
    return argparse.Namespace(config=str(cfg), dry_run=dry_run, json=False)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_deprecated_key_should_be_renamed_with_backup_when_present(tmp_path, capsys):
    original = '[runtime]\nrate_limit_action = "stop"\n'
    cfg = _write(tmp_path, original)

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 0
    assert 'transient_error_action = "stop"' in cfg.read_text()
    assert (tmp_path / "agent-runner.toml.bak").read_text() == original


def test_symlinked_config_should_back_up_beside_symlink_when_migrated(tmp_path):
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


def test_dry_run_should_write_nothing_when_deprecated_key_present(tmp_path, capsys):
    original = '[runtime]\nrate_limit_action = "stop"\n'
    cfg = _write(tmp_path, original)

    rc = migrate_cmd.cmd(_args(cfg, dry_run=True))

    assert rc == 0
    assert cfg.read_text() == original  # unchanged
    assert not (tmp_path / "agent-runner.toml.bak").exists()
    assert "dry-run" in capsys.readouterr().out.lower()


def test_migrate_should_be_noop_when_config_has_no_deprecated_keys(tmp_path):
    cfg = _write(tmp_path, '[runtime]\ntransient_error_action = "back_off"\n')

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 0
    assert not (tmp_path / "agent-runner.toml.bak").exists()


def test_migrate_should_exit_1_when_removed_key_needs_manual_transform(tmp_path):
    cfg = _write(tmp_path, "[runtime]\nround_timeout_per_phase = { dev = 900 }\n")

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 1  # human action still needed


def test_migrate_should_exit_1_and_leave_file_untouched_when_rename_would_duplicate_key(
    tmp_path,
):
    # Both the deprecated and target keys present: the rename would duplicate a
    # key, so it must NOT be applied — migrate reports the manual step (exit 1)
    # and writes nothing (no rewrite, no .bak).
    original = '[vcs]\norphan_action = "ignore"\ndirty_action = "stash"\n'
    cfg = _write(tmp_path, original)

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 1
    assert cfg.read_text() == original  # untouched
    assert not (tmp_path / "agent-runner.toml.bak").exists()


def test_migrate_should_exit_2_when_toml_is_broken(tmp_path):
    cfg = _write(tmp_path, "[runtime\n not toml")

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 2


def test_migrate_should_exit_2_when_config_cannot_be_read(tmp_path):
    cfg = tmp_path / "agent-runner.toml"
    cfg.mkdir()  # read_text() on a directory raises OSError, not TOMLDecodeError

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 2


def test_migrated_config_should_load_with_renamed_keys(tmp_path):
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
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 0

    loaded = load_config(cfg)  # was rejecting the old keys before

    assert loaded.runtime.transient_error_action == "skip"
    assert loaded.vcs.dirty_action == "ignore"


def test_bare_command_config_should_load_when_migrated(tmp_path):
    body = (
        '[agent]\ncommand = "true"\nprompt_arg_template = ["-p", "{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
    )
    cfg = _write(tmp_path, body)
    rc = migrate_cmd.cmd(_args(cfg))
    assert rc == 0

    load_config(cfg)  # the rewritten file loads clean under 0.2.12 strictness


def test_flat_phase_alias_should_exit_0_with_guidance_when_present(tmp_path, capsys):
    # The flat [phases.<name>] round_timeout_s/disable_pre_round_hooks alias is
    # a PERMANENT, still-valid form — reporting it as `manual` (exit 1 forever)
    # was the bug: `migrate` must surface it as guidance without blocking.
    cfg = _write(tmp_path, 'phases.list = ["a"]\n[phases.a]\nround_timeout_s = 900\n')

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 0
    assert "[phases.<name>.runtime]" in capsys.readouterr().out


def _guided_prefix(tmp_path: Path) -> str:
    return (
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\n'
    )


_GUIDED_NOT_CRASHED_CASES = [
    (
        "unknown_runtime_key",
        lambda tmp_path: (
            '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
            f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\nbogus = 1\n'
            f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        ),
        "bogus = 1\n",
        "",
    ),
    (
        "table_as_scalar",
        lambda tmp_path: _guided_prefix(tmp_path) + "monitor = 1\n",
        "monitor = 1\n",
        "",
    ),
    (
        "unknown_monitor_host_health_key",
        lambda tmp_path: _guided_prefix(tmp_path) + "[monitor.host_health]\nbogus = 1\n",
        "bogus = 1\n",
        "",
    ),
    (
        "unknown_agent_key",
        lambda tmp_path: (
            '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\nbogus = 1\n'
            f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
            f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        ),
        "bogus = 1\n",
        "",
    ),
    (
        "unknown_vcs_key",
        lambda tmp_path: _guided_prefix(tmp_path) + "[vcs]\nbogus = 1\n",
        "bogus = 1\n",
        "",
    ),
    (
        "unknown_monitor_key",
        lambda tmp_path: _guided_prefix(tmp_path) + "[monitor]\nbogus = 1\n",
        "bogus = 1\n",
        "",
    ),
    (
        "phases_scalar_key",
        lambda tmp_path: (
            _guided_prefix(tmp_path) + '[phases]\nlist = ["dev"]\nbogus = 1\n[phases.dev]\n'
        ),
        "bogus = 1\n",
        "",
    ),
    (
        "unknown_phase_prompt_key",
        lambda tmp_path: (
            _guided_prefix(tmp_path)
            + '[phases]\nlist = ["dev"]\n'
            + '[phases.dev.prompt]\nfiles = ["a.md"]\ninject_context = false\n'
        ),
        "inject_context = false\n",
        "",
    ),
    (
        "phase_agent_missing_placeholder",
        lambda tmp_path: (
            _guided_prefix(tmp_path)
            + '[phases]\nlist = ["dev"]\n'
            + '[phases.dev.agent]\nprompt_arg_template = ["-p"]\n'
        ),
        '[phases.dev.agent]\nprompt_arg_template = ["-p"]',
        '[phases.dev.agent]\nprompt_arg_template = ["-p", "{prompt}"]',
    ),
    (
        "argv_missing_placeholder",
        lambda tmp_path: (
            '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p"]\n'
            f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
            f'[prompt]\nfile = "{tmp_path}/p.md"\n'
        ),
        'prompt_arg_template = ["-p"]',
        'prompt_arg_template = ["-p", "{prompt}"]',
    ),
]


@pytest.mark.parametrize(
    "body_factory,replace_from,replace_to",
    [case[1:] for case in _GUIDED_NOT_CRASHED_CASES],
    ids=[case[0] for case in _GUIDED_NOT_CRASHED_CASES],
)
def test_rejected_config_should_be_guided_not_crashed_when_migrated(
    tmp_path, body_factory, replace_from, replace_to
):
    # 0.2.13/0.2.14 load_config strictness rejections (unknown keys,
    # table-as-scalar, missing {prompt} placeholders): migrate must report
    # each one (exit 1, guided) rather than crash, and applying the matching
    # hand-fix must make the config load clean again -- the migrate-parity
    # contract.
    body = body_factory(tmp_path)
    cfg = _write(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    with pytest.raises(ConfigError):
        load_config(cfg)

    rc = migrate_cmd.cmd(_args(cfg))

    assert rc == 1  # manual: guided, not a crash

    cfg.write_text(cfg.read_text().replace(replace_from, replace_to))
    load_config(cfg)  # now loads clean
