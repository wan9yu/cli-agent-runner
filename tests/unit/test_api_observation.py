from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import api
from agent_runner.api_types import Alert, ProjectState
from agent_runner.config import load_config


class _StopLoopError(Exception):
    """Sentinel raised inside patched time.sleep to break monitor_loop's while True."""


def _write_minimal_monitor_toml(work_dir: Path, log_dir: Path) -> None:
    """Write a minimal agent-runner.toml that load_config accepts."""
    prompt_file = work_dir / "prompt.md"
    prompt_file.write_text("p")
    (work_dir / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )


def _drive_monitor_loop_once(
    work_dir: Path, *, host: str | None = None, interval_s: int = 30
) -> None:
    """Drive monitor_loop through one iteration without hanging on time.sleep."""
    with (
        patch("agent_runner.api.time.sleep", side_effect=_StopLoopError),
        patch("agent_runner.api._poll_once", return_value=[]),
    ):
        gen = api.monitor_loop(work_dir, host=host, interval_s=interval_s)
        try:
            next(gen, None)
        except _StopLoopError:
            pass
        finally:
            gen.close()


def _seed_logs(work_dir: Path) -> None:
    cfg = load_config(work_dir / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:00.000Z","event":"round_start","round_num":1}\n'
        '{"ts":"2026-05-12T10:00:01.000Z","event":"agent_exit","round_num":1,"exit_code":0,"duration_s":42.0,"timed_out":false}\n'
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","round_num":1}\n'
    )
    (log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","mem_total_mb":8000,"mem_available_mb":4000,"disk_used_pct":50.0,"disk_free_gb":100.0}\n'
    )
    (log_dir / "status.json").write_text(
        json.dumps({"round_num": 1, "running": False, "last_exit_code": 0})
    )
    rounds = log_dir / "rounds"
    rounds.mkdir(parents=True, exist_ok=True)
    (rounds / "R1-2026-05-12.log").write_text("line-1\nline-2\nline-3-with-error\nline-4\nline-5\n")


def test_given_seeded_logs_when_api_peek_then_returns_project_state(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo)
    assert isinstance(state, ProjectState)
    assert len(state.defenses) == 11
    assert state.system.mem_total_mb == 8000


def test_given_state_when_peek_with_select_then_returns_subtree(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    val = api.peek(tmp_git_repo, select="system.disk_used_pct")
    assert val == 50.0


def test_given_invalid_select_when_peek_then_raises_keyerror(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    with pytest.raises(KeyError, match="nonexistent"):
        api.peek(tmp_git_repo, select="nonexistent")


def test_given_no_alerts_when_poll_once_then_returns_empty(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert alerts == []


def test_given_peek_with_round_latest_when_called_then_populates_current_round(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, round="latest")
    assert state.current_round is not None
    assert state.current_round.round_num == 1
    assert state.current_round.exit_code == 0


def test_given_peek_with_log_flag_when_called_then_populates_log_tail(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, round="latest", log=True)
    assert state.current_round is not None
    assert state.current_round.log_tail is not None
    assert "line-3-with-error" in state.current_round.log_tail


def test_given_peek_with_events_when_called_then_populates_recent_events(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, events=2)
    assert len(state.recent_events) == 2
    assert state.recent_events[-1]["event"] == "round_end"


def test_given_peek_with_missing_round_when_called_then_raises_keyerror(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    with pytest.raises(KeyError, match="round 99"):
        api.peek(tmp_git_repo, round=99)


def test_given_seeded_disk_critical_when_poll_once_then_alert_present(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    (log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","mem_total_mb":8000,"mem_available_mb":4000,"disk_used_pct":98.5,"disk_free_gb":1.0}\n'
    )
    alerts = api._poll_once(tmp_git_repo, host=None)
    crit = [a for a in alerts if a.detector == "disk_critical"]
    assert len(crit) == 1
    assert isinstance(crit[0], Alert)
    assert crit[0].auto_action == "stop_service"


def test_given_peek_json_when_emit_then_plugins_block_has_hook_and_owned_path_keys(
    tmp_git_repo: Path,
    capsys,
) -> None:
    """0.1.8: plugins block in peek JSON includes pre/post hooks + owned_paths."""
    import json

    from agent_runner.api_types import (
        ProjectState,
        ServiceMode,
        ServiceStatus,
        SystemMetrics,
    )
    from agent_runner.cli.common import emit

    state = ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )
    emit(state, json_mode=True)
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == "1.6"
    assert "pre_round_hooks" in out["plugins"]
    assert "post_round_hooks" in out["plugins"]
    assert "owned_paths" in out["plugins"]
    assert isinstance(out["plugins"]["pre_round_hooks"], list)
    assert isinstance(out["plugins"]["post_round_hooks"], list)
    assert isinstance(out["plugins"]["owned_paths"], list)


def test_given_events_with_hook_failures_when_state_assembled_then_filtered_to_field(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.1.8: api.peek populates ProjectState.recent_hook_failures from parsed events."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text(
        '{"ts":"2026-05-13T00:00:00.000Z","event":"round_start","round_num":1}\n'
        '{"ts":"2026-05-13T00:01:00.000Z","event":"hook_failed","hook_name":"X","hook_kind":"pre"}\n'
        '{"ts":"2026-05-13T00:02:00.000Z","event":"agent_exit","exit_code":0,"round_num":1,"duration_s":1.0,"timed_out":false}\n'
        '{"ts":"2026-05-13T00:03:00.000Z","event":"hook_failed","hook_name":"Y","hook_kind":"post"}\n'
    )
    state = api.peek(tmp_git_repo)
    assert len(state.recent_hook_failures) == 2
    assert all(e["event"] == "hook_failed" for e in state.recent_hook_failures)
    names = sorted(e["hook_name"] for e in state.recent_hook_failures)
    assert names == ["X", "Y"]


def test_given_fresh_project_no_log_dir_when_monitor_loop_starts_then_creates_dir_and_emits(
    tmp_path: Path,
) -> None:
    """monitor_loop creates log_dir if missing before emitting monitor_started."""
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    _write_minimal_monitor_toml(work_dir, log_dir)
    assert not log_dir.exists()

    _drive_monitor_loop_once(work_dir)

    assert log_dir.exists(), "monitor_loop should have created log_dir"
    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "monitor_started should have been emitted"
    payload = json.loads(events_files[-1].read_text(encoding="utf-8").strip())
    assert payload["event"] == "monitor_started"


def test_given_monitor_loop_when_started_then_emits_monitor_started_event(
    tmp_path: Path,
) -> None:
    """monitor_loop() emits a monitor_started event before its first poll."""
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)

    _drive_monitor_loop_once(work_dir)

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "expected at least one events file written"
    lines = events_files[-1].read_text(encoding="utf-8").splitlines()
    all_events = [json.loads(line) for line in lines]
    started = [e for e in all_events if e.get("event") == "monitor_started"]
    assert len(started) == 1, f"expected exactly one monitor_started event, got {len(started)}"
    payload = started[0]
    assert payload["host"] is None
    assert payload["interval_s"] == 30
    assert payload["mode"] == "anomaly-only"
    assert payload["log_dir"] == str(log_dir)


def test_given_work_dir_with_shell_metachars_when_project_name_then_raises(tmp_path: Path) -> None:
    """Project name (work_dir basename) must reject shell metacharacters."""
    bad_dir = tmp_path / "foo;rm -rf /"
    bad_dir.mkdir()
    with pytest.raises(ValueError, match="invalid project name"):
        api._project_name(bad_dir)


def test_given_clean_work_dir_when_project_name_then_returns_basename(tmp_path: Path) -> None:
    """Project name passes through for valid basenames."""
    good_dir = tmp_path / "my-project_v1.2"
    good_dir.mkdir()
    assert api._project_name(good_dir) == "my-project_v1.2"


def test_given_two_blips_then_recovery_when_monitor_loop_then_blips_logged_no_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """monitor_loop tolerates transient blips and emits one event per attempt."""
    from agent_runner.monitor import MonitorRemoteError

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)

    call_count = [0]

    def fake_poll_once(*_args, **_kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise MonitorRemoteError("pi", "ssh: connect: Connection refused")
        return []  # success on 3rd call

    monkeypatch.setattr(api, "_poll_once", fake_poll_once)

    # 3 sleeps fire in this scenario: backoff(1s), backoff(2s), interval_s(30)
    # The third must raise _StopLoopError to break out of the while True loop.
    with patch("agent_runner.api.time.sleep", side_effect=[None, None, _StopLoopError()]):
        gen = api.monitor_loop(work_dir, host="pi", interval_s=30)
        try:
            next(gen, None)
        except _StopLoopError:
            pass
        finally:
            gen.close()

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    all_events = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [e for e in all_events if e["event"] == "monitor_remote_blip"]
    giveups = [e for e in all_events if e["event"] == "monitor_remote_giveup"]
    assert len(blips) == 2, f"expected 2 blips, got {[e['event'] for e in all_events]}"
    assert len(giveups) == 0
    assert blips[0]["attempt"] == 1
    assert blips[1]["attempt"] == 2
    assert "Connection refused" in blips[0]["error"]


def test_given_persistent_failure_when_monitor_loop_then_emits_giveup_and_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past cap_s, monitor_loop emits monitor_remote_giveup and propagates."""
    from agent_runner.monitor import MonitorRemoteError

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)

    # Override the TOML to use a tiny tolerance so we can exhaust quickly
    (work_dir / "agent-runner.toml").write_text(
        (work_dir / "agent-runner.toml").read_text() + "[monitor]\nremote_failure_tolerance_s = 2\n"
    )

    def always_fail(*_args, **_kwargs):
        raise MonitorRemoteError("pi", "ssh: down")

    monkeypatch.setattr(api, "_poll_once", always_fail)

    # Speed up wall clock: monotonic returns 0, 1, 2, 3, ... so cap_s=2 is hit
    fake_clock = [0.0]

    def fake_monotonic():
        v = fake_clock[0]
        fake_clock[0] += 1.0
        return v

    monkeypatch.setattr("agent_runner.api.time.monotonic", fake_monotonic)
    monkeypatch.setattr("agent_runner.api.time.sleep", lambda _s: None)

    gen = api.monitor_loop(work_dir, host="pi", interval_s=30)
    with pytest.raises(MonitorRemoteError):
        next(gen, None)
    gen.close()

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    all_events = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    giveups = [e for e in all_events if e["event"] == "monitor_remote_giveup"]
    assert len(giveups) == 1
    assert giveups[0]["host"] == "pi"
    assert giveups[0]["cap_s"] == 2
    assert giveups[0]["total_attempts"] == 3


def test_given_tolerance_zero_when_blip_then_raises_immediately_no_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remote_failure_tolerance_s=0 preserves 0.1.10 immediate-propagate."""
    from agent_runner.monitor import MonitorRemoteError

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)
    (work_dir / "agent-runner.toml").write_text(
        (work_dir / "agent-runner.toml").read_text() + "[monitor]\nremote_failure_tolerance_s = 0\n"
    )

    def always_fail(*_args, **_kwargs):
        raise MonitorRemoteError("pi", "ssh: down")

    monkeypatch.setattr(api, "_poll_once", always_fail)

    gen = api.monitor_loop(work_dir, host="pi", interval_s=30)
    with pytest.raises(MonitorRemoteError):
        next(gen, None)
    gen.close()

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    all_events = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [e for e in all_events if e["event"] == "monitor_remote_blip"]
    giveups = [e for e in all_events if e["event"] == "monitor_remote_giveup"]
    assert blips == []
    assert giveups == []


def test_given_blip_then_success_when_monitor_loop_then_state_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a successful poll, blip counter resets so the next blip is attempt=1."""
    from agent_runner.monitor import MonitorRemoteError

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)

    sequence = iter(
        [
            MonitorRemoteError("pi", "blip 1"),  # blip
            [],  # success — resets state
            MonitorRemoteError("pi", "blip 2"),  # blip again, should be attempt=1
            _StopLoopError(),  # break the loop
        ]
    )

    def fake_poll_once(*_args, **_kwargs):
        item = next(sequence)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(api, "_poll_once", fake_poll_once)
    monkeypatch.setattr("agent_runner.api.time.sleep", lambda _s: None)

    gen = api.monitor_loop(work_dir, host="pi", interval_s=30)
    try:
        while True:
            next(gen)  # no default — let exceptions propagate naturally
    except (StopIteration, _StopLoopError):
        pass
    finally:
        gen.close()

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    all_events = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [e for e in all_events if e["event"] == "monitor_remote_blip"]
    assert len(blips) == 2
    assert blips[0]["attempt"] == 1
    assert blips[1]["attempt"] == 1, "successful poll should have reset the counter"
