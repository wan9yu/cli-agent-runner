from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import api, defenses
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
        patch("agent_runner.clock.SYSTEM_CLOCK.sleep", side_effect=_StopLoopError),
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
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","mem_total_mb":8000,'
        '"mem_available_mb":4000,"mem_free_mb":3000,"disk_used_pct":50.0,"disk_free_gb":100.0}\n'
    )
    (log_dir / "status.json").write_text(
        json.dumps({"round_num": 1, "running": False, "last_exit_code": 0})
    )
    rounds = log_dir / "rounds"
    rounds.mkdir(parents=True, exist_ok=True)
    (rounds / "R1-2026-05-12.log").write_text("line-1\nline-2\nline-3-with-error\nline-4\nline-5\n")


def test_peek_should_return_project_state_when_logs_seeded(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")

    state = api.peek(tmp_git_repo)

    assert isinstance(state, ProjectState)
    assert len(state.defenses) == len(defenses.catalog(cfg))
    assert state.system.mem_total_mb == 8000


def test_peek_should_return_subtree_when_select_specified(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)

    val = api.peek(tmp_git_repo, select="system.disk_used_pct")

    assert val == 50.0


def test_peek_should_raise_keyerror_when_select_invalid(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)

    with pytest.raises(KeyError, match="nonexistent"):
        api.peek(tmp_git_repo, select="nonexistent")


def test_poll_once_should_return_empty_when_no_alerts(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)

    # Disable supervisor_stale: seeded events use a fixed old timestamp; the
    # detector would otherwise fire because now >> seed ts.
    real_load = load_config

    def patched_load(path):
        cfg = real_load(path)
        return dataclasses.replace(
            cfg,
            monitor=dataclasses.replace(cfg.monitor, supervisor_stale_threshold_s=0),
        )

    monkeypatch.setattr("agent_runner.api.load_config", patched_load)

    alerts = api._poll_once(tmp_git_repo)

    assert alerts == []


def test_peek_should_populate_current_round_when_round_is_latest(
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


def test_peek_should_populate_log_tail_when_log_flag_set(
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


def test_peek_should_populate_recent_events_when_events_count_given(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)

    state = api.peek(tmp_git_repo, events=2)

    assert len(state.recent_events) == 2
    assert state.recent_events[-1]["event"] == "round_end"


def test_peek_should_raise_keyerror_when_round_missing(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)

    with pytest.raises(KeyError, match="round 99"):
        api.peek(tmp_git_repo, round=99)


def test_poll_once_should_return_disk_critical_alert_when_disk_critical_seeded(
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

    alerts = api._poll_once(tmp_git_repo)

    crit = [a for a in alerts if a.detector == "disk_critical"]
    assert len(crit) == 1
    assert isinstance(crit[0], Alert)
    assert crit[0].auto_action == "stop_service"


def test_emit_should_populate_plugin_hook_and_owned_path_keys_when_json_mode(
    tmp_git_repo: Path,
    capsys,
) -> None:
    """0.1.8: plugins block in peek JSON includes pre/post hooks + owned_paths."""
    from agent_runner.api_types import (
        ProjectState,
        ServiceMode,
        ServiceStatus,
        SystemMetrics,
    )
    from agent_runner.cli.common import PEEK_SCHEMA_VERSION, emit

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
    assert out["schema_version"] == PEEK_SCHEMA_VERSION
    assert "pre_round_hooks" in out["plugins"]
    assert "post_round_hooks" in out["plugins"]
    assert "owned_paths" in out["plugins"]
    assert isinstance(out["plugins"]["pre_round_hooks"], list)
    assert isinstance(out["plugins"]["post_round_hooks"], list)
    assert isinstance(out["plugins"]["owned_paths"], list)


def test_peek_should_populate_recent_hook_failures_when_events_contain_hook_failures(
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


def test_monitor_loop_should_create_log_dir_and_emit_started_event_when_log_dir_missing(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    _write_minimal_monitor_toml(work_dir, log_dir)
    assert not log_dir.exists()

    _drive_monitor_loop_once(work_dir)

    assert log_dir.exists(), "monitor_loop should have created log_dir"
    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "monitor_started should have been emitted"
    lines = events_files[-1].read_text(encoding="utf-8").splitlines()
    all_events = [json.loads(line) for line in lines]
    started = [e for e in all_events if e.get("event") == "monitor_started"]
    assert len(started) == 1, f"expected exactly one monitor_started event, got {len(started)}"
    payload = started[0]
    assert payload["host"] is None
    assert payload["interval_s"] == 30
    assert payload["mode"] == "anomaly-only"
    assert payload["log_dir"] == str(log_dir)


def test_monitor_loop_should_raise_before_first_poll_when_host_given(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection is on-host by design — reject --host at startup.

    The detectors read the supervised host's own logs and stop its own service;
    there is no remote polling mode (remote observation is the event relay).
    The rejection must land before the first poll and before monitor_started.
    """
    from agent_runner.monitor import MonitorRemoteUnsupportedError

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    _write_minimal_monitor_toml(work_dir, log_dir)

    def never_polls(*_args, **_kwargs):
        raise AssertionError("remote monitor must not poll")

    monkeypatch.setattr(api, "_poll_once", never_polls)

    with pytest.raises(MonitorRemoteUnsupportedError) as exc_info:
        api.monitor_loop(work_dir, host="pi", interval_s=60)

    msg = str(exc_info.value)
    assert "--host pi" in msg
    assert "detection runs on the supervised host by design" in msg
    assert "ssh pi" in msg
    assert "--mode events" in msg, "the message must point at the supported remote mode"
    assert "docs/runbook.md" in msg
    assert exc_info.value.host == "pi"
    assert not sorted(log_dir.glob("events-*.jsonl")), (
        "monitor_started must not be emitted — supervision never came up"
    )


def test_project_name_should_raise_when_work_dir_has_shell_metachars(tmp_path: Path) -> None:
    bad_dir = tmp_path / "foo;rm -rf /"
    bad_dir.mkdir()

    with pytest.raises(ValueError, match="invalid project name"):
        api._project_name(bad_dir)


def test_project_name_should_return_basename_when_work_dir_clean(tmp_path: Path) -> None:
    good_dir = tmp_path / "my-project_v1.2"
    good_dir.mkdir()

    assert api._project_name(good_dir) == "my-project_v1.2"


def test_peek_should_populate_recent_blips_when_events_contain_blips(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    # Append 7 blip events to the seeded events file
    blip_lines = []
    for i in range(7):
        blip_lines.append(
            json.dumps(
                {
                    "ts": f"2026-05-12T11:00:0{i}.000Z",
                    "event": "agent_network_blip",
                    "round_num": i,
                    "phase": "main",
                    "matched": "connection refused",
                    "round_duration_s": 1.0,
                    "exit_code": 1,
                    "timed_out": False,
                }
            )
        )
    with (log_dir / "events-2026-05.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n".join(blip_lines) + "\n")

    state = api.peek(tmp_git_repo)

    assert len(state.recent_blips) == 5, "default limit is 5, most-recent first"
    # The 5 returned should be the last 5 in chronological order (rounds 2..6)
    rounds = [b["round_num"] for b in state.recent_blips]
    assert rounds == [2, 3, 4, 5, 6]


def test_narrate_events_should_yield_formatted_lines_when_events_seeded(
    tmp_path: Path,
) -> None:
    from agent_runner.api import narrate_events

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    events_file = log_dir / "events-2026-05.jsonl"
    events_file.write_text(
        '{"ts":"2026-05-14T12:00:00.123Z","event":"round_start","round_num":1,"phase":"dev"}\n'
        '{"ts":"2026-05-14T12:00:01.456Z","event":"agent_spawn","round_num":1,"pid":12345}\n'
    )

    # Drive the generator and collect 2 lines (poll_interval_s tiny to avoid wait)
    gen = narrate_events(log_dir, poll_interval_s=0.01)
    lines = [next(gen) for _ in range(2)]
    gen.close()

    assert "[12:00:00.123]" in lines[0]
    assert "round_start" in lines[0]
    assert "round=1" in lines[0]
    assert "phase=dev" in lines[0]
    assert "[12:00:01.456]" in lines[1]
    assert "agent_spawn" in lines[1]
    assert "pid=12345" in lines[1]


def test_peek_should_return_rate_limit_state_when_supervisor_throttled(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from agent_runner.api_types import RateLimitState

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    future = int(time.time() + 3600)
    (log_dir / "events-2026-05.jsonl").write_text(
        json.dumps(
            {
                "event": "transient_error_detected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": future,
                "classification": "rate_limit_account",
                "round_num": 42,
            }
        )
        + "\n"
    )

    state = api.peek(tmp_git_repo)

    assert state.service.rate_limit is not None
    assert isinstance(state.service.rate_limit, RateLimitState)
    assert state.service.rate_limit.throttled_until_epoch == future
    assert state.service.rate_limit.limit_type == "rate_limit_account"
    assert state.service.rate_limit.since_round == 42
    assert state.service.rate_limit.throttled_agents == ("claude",)  # plural view


def test_peek_should_surface_throttled_agent_when_sibling_recovers(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """peek must not drop rate_limit to null when the newest transient event is a
    sibling agent's recovered while another agent is still throttled — the scalar
    global-latest view is None there, but throttled_agents must still list the active
    agent (the multi-provider case the field exists for)."""
    import time

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    future = int(time.time() + 3600)
    past = int(time.time() - 60)
    rows = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "classification": "rate_limit_account",
            "round_num": 7,
        },
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:01:00Z",
            "agent": "gemini",
            "reset_at_epoch": past,
            "classification": "rate_limit_model",
            "round_num": 8,
        },
        {
            "event": "transient_error_recovered",
            "ts": "2026-05-16T00:02:00Z",
            "agent": "gemini",
            "throttled_for_s": 60,
            "classification": "rate_limit_model",
        },
    ]
    (log_dir / "events-2026-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    state = api.peek(tmp_git_repo)

    assert state.service.rate_limit is not None  # not dropped to null
    assert state.service.rate_limit.throttled_agents == ("claude",)
    assert state.service.rate_limit.agent == "claude"
    assert state.service.rate_limit.throttled_until_epoch == future


def test_peek_should_report_escalated_reset_when_consecutive_5xx_detected(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """peek's throttled_until_epoch must reflect the events-derived exp-backoff
    escalation (_backoff_exponent + _extend_reset), not the raw emitter reset — a
    permanently-failing agent's 4x-extended reset must be visible to an operator
    watching peek / the HTTP dashboard, not just gate the skip loop internally."""
    import time

    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    raw_reset = int(time.time() + 60)
    # 3 consecutive detections, no success between → exponent=2 → multiplier=4.
    rows = [
        {
            "event": "transient_error_detected",
            "ts": f"2026-05-16T00:0{i}:00Z",
            "agent": "claude",
            "reset_at_epoch": raw_reset,
            "classification": "api_transient_5xx",
            "round_num": i,
        }
        for i in range(3)
    ]
    (log_dir / "events-2026-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    base = _BACK_OFF_DEFAULTS["api_transient_5xx"]
    expected_reset = raw_reset + base * (2**2 - 1)  # exponent=2 → 4x - 1x extra

    state = api.peek(tmp_git_repo)

    assert state.service.rate_limit is not None
    assert state.service.rate_limit.throttled_until_epoch == expected_reset
    assert state.service.rate_limit.throttled_until_epoch > raw_reset


def test_emit_should_include_disabled_plugins_block_when_json_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """peek --json includes plugins.disabled sub-key (verbatim from disabled_plugin_names())."""
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
    assert "plugins" in out
    assert "disabled" in out["plugins"]
    # Default empty list when no apply_plugin_disable was called
    assert isinstance(out["plugins"]["disabled"], list)
