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


def test_given_happy_path_when_run_upgrade_then_emits_service_upgraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: stop → pip install → smoke → start → emit service_upgraded."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        if cmd[0] == "pip":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 0.1.99\n", stderr=""
            )
        if "peek" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(tmp_path / "agent-runner.toml")
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=tmp_path / "agent-runner.toml")
    assert rc == 0

    pip_calls = [c for c in call_log if c[0] == "pip"]
    assert len(pip_calls) == 1
    assert "cli-agent-runner==0.1.99" in pip_calls[0]

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    upgrades = [p for p in payloads if p["event"] == "service_upgraded"]
    assert len(upgrades) == 1
    assert upgrades[0]["to_version"] == "0.1.99"
    assert "from_version" in upgrades[0]
    assert "duration_s" in upgrades[0]


def test_given_no_target_when_run_upgrade_then_pip_uses_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--target` not supplied → pip install --upgrade cli-agent-runner (no version pin)."""
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        if cmd[0] == "pip":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 0.1.50\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(tmp_path / "agent-runner.toml")
    rc = upgrade_cmd._run_upgrade(cfg, target=None, cfg_path=tmp_path / "agent-runner.toml")
    assert rc == 0
    pip_calls = [c for c in call_log if c[0] == "pip"]
    assert len(pip_calls) == 1
    assert "cli-agent-runner" in pip_calls[0]
    assert "==" not in " ".join(pip_calls[0])


def test_given_pip_install_fails_when_run_upgrade_then_no_event_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pip install rc!=0 → service stopped, no event emitted, exit 1."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "pip":
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="ERROR: Could not find package"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(tmp_path / "agent-runner.toml")
    rc = upgrade_cmd._run_upgrade(cfg, target="bogus", cfg_path=tmp_path / "agent-runner.toml")
    assert rc == 1

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        assert not any(p["event"].startswith("service_upgrad") for p in payloads)
