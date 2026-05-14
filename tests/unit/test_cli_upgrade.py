from __future__ import annotations

from pathlib import Path

import pytest


def test_given_upgrade_subcommand_with_target_when_main_then_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`agent-runner upgrade --target X.Y.Z` parses + dispatches to upgrade_cmd.cmd."""
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        captured["config"] = getattr(args, "config", None)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    rc = main(["--config", str(tmp_path / "agent-runner.toml"), "upgrade", "--target", "0.1.99"])
    assert rc == 0
    assert captured["target"] == "0.1.99"


def test_given_upgrade_no_target_when_main_then_target_defaults_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`agent-runner upgrade` (no --target) → args.target is None (= latest)."""
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    rc = main(["--config", str(tmp_path / "agent-runner.toml"), "upgrade"])
    assert rc == 0
    assert captured["target"] is None
