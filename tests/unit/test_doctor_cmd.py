from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_runner.cli import doctor_cmd
from agent_runner.cli._serve_cgroup import brake_report_state
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections, write_min_config

TZ = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    "enabled, delegated, expected",
    [
        (False, True, "off"),
        (False, None, "off"),
        (True, True, "armed"),
        (True, False, "inert(undelegated)"),
        (True, None, "inert(no cgroup v2)"),
    ],
)
def test_brake_report_state_should_map_config_and_delegation_to_a_label_when_invoked(
    enabled, delegated, expected
) -> None:
    assert brake_report_state(enabled, delegated) == expected


class _FakeProc:
    """Stand-in for a real subprocess.Popen handle -- just enough surface for
    vcs_state._run_with_timeout's communicate()/returncode use so the
    SANCTIONED git rev-parse probe (work_dir_is_git_repo) completes normally
    under the monkeypatch below instead of crashing on a bare recorder."""

    returncode = 1

    def communicate(self, timeout=None):
        return "", ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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


def _write_paused_config(tmp_path: Path) -> Path:
    """A "wait"-policy phase whose window is closed at 10:00 and reopens at
    12:00 -- paused with a known, computable resume_at."""
    return make_toml_with_sections(
        tmp_path,
        phases_block=(
            '[schedule]\ntimezone = "Asia/Shanghai"\n'
            '[phases]\nlist = ["a"]\nphase_policy = "wait"\n'
            '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n'
        ),
    )


def _cfg_with_colliding_phases(tmp_path: Path):
    """Two agent-overriding phases sharing an always-open, never-paused
    window -- a guaranteed config-level overlap."""
    phases_block = (
        '[phases]\nlist = ["a", "b"]\n'
        '[phases.a.agent]\nname = "phase-a"\n'
        '[phases.a.schedule]\nrun_windows = ["00:00-24:00"]\n'
        '[phases.b.agent]\nname = "phase-b"\n'
        '[phases.b.schedule]\nrun_windows = ["00:00-24:00"]\n'
    )
    return load_config(make_toml_with_sections(tmp_path, phases_block=phases_block))


def _write_never_opens_config(tmp_path: Path) -> Path:
    """A global pause window covering the whole day -- paused with no opening
    candidate in the horizon, so Selection.resume_at stays None."""
    return make_toml_with_sections(
        tmp_path,
        phases_block='[schedule]\ntimezone = "Asia/Shanghai"\npause_windows = ["00:00-24:00"]\n',
    )


def test_doctor_should_report_each_battery_check_when_config_loaded(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "config_loaded" in out
    assert "agent_cli_in_path" in out


def test_doctor_should_print_phase_plan_for_requested_rounds_when_invoked(tmp_path, capsys):
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


def test_doctor_should_include_resume_at_isoformat_in_plan_when_a_round_is_paused(
    tmp_path, monkeypatch, capsys
):
    # 10:00 sits inside the 09:00-12:00 pause window -> paused, resume at 12:00.

    monkeypatch.setattr(
        SYSTEM_CLOCK, "now_in_zone", lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ)
    )
    expected_resume_at = datetime(2026, 8, 22, 12, 0, tzinfo=TZ)
    args = _args(_write_paused_config(tmp_path), json=True)

    doctor_cmd.cmd_doctor(args)

    payload = json.loads(capsys.readouterr().out)
    paused = [s for s in payload["plan"] if s["paused"]][0]
    assert paused["resume_at"] == expected_resume_at.isoformat()


def test_doctor_should_not_render_resume_when_paused_round_has_no_resume_at(tmp_path, capsys):
    # the all-day pause window never opens within the search horizon -> Selection(resume_at=None).

    args = _args(_write_never_opens_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "resumes None" not in out
    assert "at None" not in out


def test_doctor_should_report_phase_window_overlap_as_a_failing_check_when_present(tmp_path):
    from agent_runner import startup_check

    cfg = _cfg_with_colliding_phases(tmp_path)

    results = startup_check.run_battery(cfg)

    overlap_check = next(r for r in results if r.name == "phase_window_overlap")
    assert overlap_check.ok is False


def test_doctor_should_report_cgroup_delegation_state_in_json_when_invoked(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path), json=True)

    doctor_cmd.cmd_doctor(args)

    payload = json.loads(capsys.readouterr().out)
    assert "cgroup" in payload
    assert "delegated" in payload["cgroup"]
    assert "cgroup_path" in payload["cgroup"]
    assert "memory_high" in payload["cgroup"]


def test_doctor_should_report_brake_state_in_text_and_json_when_invoked(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path), json=True)

    doctor_cmd.cmd_doctor(args)

    payload = json.loads(capsys.readouterr().out)
    assert "brake" in payload["cgroup"]
    assert payload["cgroup"]["brake"] in (
        "off",
        "armed",
        "inert(undelegated)",
        "inert(no cgroup v2)",
    )

    text_args = _args(_write_min_config(tmp_path))
    doctor_cmd.cmd_doctor(text_args)
    out = capsys.readouterr().out
    assert "brake:" in out


def test_doctor_should_never_emit_an_event_when_probing_cgroup(tmp_path, monkeypatch):
    from agent_runner import events

    def _fail_if_called(*a, **k):
        raise AssertionError("doctor must never emit an event")

    monkeypatch.setattr(events, "emit", _fail_if_called)
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)


def test_doctor_should_print_cooperative_preset_names_when_invoked(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "cooperative presets:" in out


def test_doctor_should_omit_the_inert_hint_when_brake_off_and_nothing_bound(
    tmp_path, capsys, monkeypatch
):
    from agent_runner import metrics

    monkeypatch.setattr(
        metrics,
        "cgroup_memory_limits",
        lambda **_k: {"cgroup_path": "/x.service", "memory_max": None, "memory_swap_max": None},
    )
    monkeypatch.setattr(metrics, "cgroup_memory_high", lambda **_k: None)
    monkeypatch.setattr(metrics, "cgroup_delegated", lambda **_k: False)
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "brake: off" in out
    assert "soft-brake is inert" not in out


def test_doctor_should_show_the_inert_hint_when_a_limit_is_bound_but_undelegated(
    tmp_path, capsys, monkeypatch
):
    from agent_runner import metrics

    monkeypatch.setattr(
        metrics,
        "cgroup_memory_limits",
        lambda **_k: {
            "cgroup_path": "/x.service",
            "memory_max": 512 * 1024 * 1024,
            "memory_swap_max": None,
        },
    )
    monkeypatch.setattr(metrics, "cgroup_memory_high", lambda **_k: None)
    monkeypatch.setattr(metrics, "cgroup_delegated", lambda **_k: False)
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "soft-brake is inert" in out


def test_doctor_should_print_resolved_sigterm_grace_when_invoked(tmp_path, capsys):
    args = _args(_write_min_config(tmp_path))

    doctor_cmd.cmd_doctor(args)

    out = capsys.readouterr().out
    assert "sigterm grace:" in out
