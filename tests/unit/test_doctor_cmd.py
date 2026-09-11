from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_runner.cli import doctor_cmd
from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections, write_min_config


class _FakeProc:
    """Stand-in for a real subprocess.Popen handle -- just enough surface for
    vcs_state._run_with_timeout's communicate()/returncode use so the
    SANCTIONED git rev-parse probe (work_dir_is_git_repo) completes normally
    under the monkeypatch below instead of crashing on a bare recorder."""

    returncode = 1

    def communicate(self, timeout=None):
        return "", ""


def _fake_popen(calls: list[list[str]]):
    def make(argv, *a, **k):
        calls.append(argv)
        return _FakeProc()

    return make


def _args(config: Path, *, rounds: int = 3, json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(config=str(config), rounds=rounds, json=json)


def _write_min_config(tmp_path: Path) -> Path:
    return write_min_config(tmp_path)


def _write_phase_config(tmp_path: Path) -> Path:
    return make_toml_with_sections(tmp_path, phases_block='[phases]\nlist = ["dev"]\n')


def test_doctor_should_report_each_battery_check_when_config_loaded(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "config_loaded" in out
    assert "agent_cli_in_path" in out


def test_doctor_should_print_phase_plan_for_requested_rounds(tmp_path, capsys):
    args = _args(_write_phase_config(tmp_path), rounds=2)

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "round 1" in out
    assert "round 2" in out


def test_doctor_should_emit_json_when_json_flag_set(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path), json=True)

    doctor_cmd.cmd_doctor(args)

    payload = json.loads(capsys.readouterr().out)
    assert "checks" in payload and "plan" in payload and "overlaps" in payload


def test_doctor_should_not_launch_the_agent_when_run(tmp_path, monkeypatch, capsys):
    # work_dir_is_git_repo's bounded, read-only `git rev-parse` is the same
    # sanctioned probe serve runs at boot -- explicitly ALLOWED here. The
    # guarantee doctor actually owes is "no agent round is launched."
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", _fake_popen(calls))
    config = _write_min_config(tmp_path)
    agent_binary = load_config(config).agent.command[0]
    args = _args(config)

    doctor_cmd.cmd_doctor(args)

    assert any(argv[0] == "git" for argv in calls)  # proves the probe still ran (non-vacuous)
    assert not any(argv[0] == agent_binary for argv in calls)
