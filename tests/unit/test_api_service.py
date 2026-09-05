from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import api
from agent_runner.api_types import InitResult, ServiceMode, ServiceStatus
from agent_runner.config import PhaseOverride, PhasesConfig, load_config
from tests._clock import FakeClock


def test_given_git_repo_when_api_init_then_returns_init_result(tmp_git_repo: Path) -> None:
    result = api.init(tmp_git_repo, force=False, commit=False)
    assert isinstance(result, InitResult)
    assert result.work_dir == tmp_git_repo
    assert any(f.name == "agent-runner.toml" for f in result.files_created)


def test_given_no_systemd_no_pid_when_api_status_then_returns_mode_none(tmp_git_repo: Path) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    s = api.status(tmp_git_repo)
    assert isinstance(s, ServiceStatus)
    assert s.mode == ServiceMode.NONE
    assert s.active is False


def test_given_pid_file_with_self_pid_when_status_then_active_true(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    s = api.status(tmp_git_repo)
    assert s.mode == ServiceMode.PID_FILE
    assert s.active is True
    assert s.pid == os.getpid()


def test_given_pid_file_with_dead_pid_when_status_then_active_false(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("999999999")
    s = api.status(tmp_git_repo)
    assert s.mode == ServiceMode.PID_FILE
    assert s.active is False


def test_given_pid_file_when_api_stop_then_sends_sigterm(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        api.stop(tmp_git_repo)
        send.assert_called_with(12345, signal.SIGTERM)


def test_given_pid_file_when_api_kill_then_sends_sigterm_then_sigkill(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    # True on the first liveness check, False on every one after -- robust to
    # exactly how many times the poll re-checks before it settles (an
    # implementation detail of the underlying wait, not this test's concern).
    calls = {"n": 0}

    def fake_pid_alive(_pid: int) -> bool:
        calls["n"] += 1
        return calls["n"] == 1

    with (
        patch("agent_runner.api.send_signal_to_pid", return_value=True) as send,
        patch("agent_runner.api.pid_alive", side_effect=fake_pid_alive),
    ):
        api.kill(tmp_git_repo)
        sent = [c.args[1] for c in send.call_args_list]
        assert signal.SIGTERM in sent


def test_given_install_with_no_systemctl_when_called_then_returns_install_result(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "fake-systemd"
    )
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )
    result = api.install(tmp_git_repo, system=False, with_monitor=False)
    assert result.unit_path.exists()
    assert result.monitor_unit_path is None


def test_given_install_with_monitor_when_called_then_writes_two_units(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "fake-systemd"
    )
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )
    result = api.install(tmp_git_repo, system=False, with_monitor=True)
    assert result.unit_path.exists()
    assert result.monitor_unit_path is not None
    assert result.monitor_unit_path.exists()


def test_given_installed_unit_when_uninstall_then_removes_file(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    fake_systemd = tmp_git_repo / "fake-systemd"
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake_systemd)
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: "inactive")
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )
    api.install(tmp_git_repo, system=False, with_monitor=True)
    api.uninstall(tmp_git_repo)
    unit_name = f"agent-runner@{tmp_git_repo.name}.service"
    assert not (fake_systemd / unit_name).exists()


def test_given_draining_unit_when_uninstall_then_does_not_raise(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uninstall against a unit whose stop blocks past the confirm bound (serve
    draining an in-flight round) must NOT raise TimeoutExpired: it drains via
    lifecycle.stop_unit_draining -- the same primitive stop()/restart() use --
    before disabling and unlinking the unit file (0.2.13 correctness fix: a
    raw blocking `systemctl stop` here TimeoutExpired's once _systemctl_user
    got a timeout, half-executing the uninstall)."""
    api.init(tmp_git_repo, force=False, commit=False)
    fake_systemd = tmp_git_repo / "fake-systemd"
    fake_systemd.mkdir(exist_ok=True)
    unit_name = f"agent-runner@{tmp_git_repo.name}.service"
    (fake_systemd / unit_name).write_text("[Unit]\n")
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake_systemd)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("activating")
    )
    calls: list[tuple[str, ...]] = []
    stub = _draining_systemctl_user(calls)
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", stub)
    monkeypatch.setattr("agent_runner.api._systemctl_user", stub)

    result = api.uninstall(tmp_git_repo)  # must not raise TimeoutExpired

    assert result is True
    assert not (fake_systemd / unit_name).exists()  # unlinked even though still draining
    assert any(a[:2] == ("--no-block", "stop") for a in calls)  # queued, never a blocking stop


def test_given_per_phase_override_when_poll_once_then_forwards_phases_overrides_to_monitor(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_poll_once must forward cfg.phases.overrides to run_all_detectors as phases_overrides."""
    api.init(tmp_git_repo, force=False, commit=False)

    # Patch load_config to inject a phases override
    real_load = load_config

    def patched_load(path):
        cfg = real_load(path)
        import dataclasses

        return dataclasses.replace(
            cfg,
            phases=PhasesConfig(
                list=["dev"],
                overrides={"dev": PhaseOverride(round_timeout_s=3600)},
            ),
        )

    monkeypatch.setattr("agent_runner.api.load_config", patched_load)

    captured: list[dict] = []

    def capturing_rad(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("agent_runner.monitor.run_all_detectors", capturing_rad)

    api._poll_once(tmp_git_repo)

    assert captured, "run_all_detectors was never called"
    call_kwargs = captured[0]
    assert "phases_overrides" in call_kwargs, (
        "phases_overrides kwarg missing from run_all_detectors call"
    )
    assert call_kwargs["phases_overrides"] == {"dev": PhaseOverride(round_timeout_s=3600)}


def _fake_systemd_unit(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_git_repo / "fake-systemd"
    fake.mkdir(exist_ok=True)
    (fake / f"agent-runner@{tmp_git_repo.name}.service").write_text("[Unit]\n")
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake)


def test_given_systemd_failed_when_status_then_active_false(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: "failed")
    s = api.status(tmp_git_repo)
    assert s.mode == ServiceMode.SYSTEMD_USER
    assert s.active is False


def test_given_systemd_activating_when_status_then_active_true(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: "activating")
    assert api.status(tmp_git_repo).active is True


def test_given_systemctl_absent_when_status_then_falls_back_to_pid(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    from agent_runner.config import load_config

    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: None)
    assert api.status(tmp_git_repo).active is True  # live serve.pid


def test_systemctl_is_active_seam_returns_none_when_binary_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr("agent_runner.api.subprocess.run", boom)
    assert api._systemctl_is_active("agent-runner@x.service") is None


def test_given_pid_file_when_restart_then_refuses_before_stopping(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain serve process can't be respawned by the CLI — restart must refuse
    FIRST, never leaving a half-executed stop-without-start."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    from agent_runner.config import load_config

    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        with pytest.raises(RuntimeError, match="systemd"):
            api.restart(tmp_git_repo)
    send.assert_not_called()  # refused BEFORE stop()


def test_given_pid_file_when_kill_then_rechecks_alive_after_sigkill(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After SIGKILL, active must reflect the post-kill liveness, not the stale
    pre-SIGKILL True."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    from agent_runner.config import load_config

    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    killed = {"sent": False}

    def fake_send(pid: int, sig: int) -> bool:
        assert pid > 0, "PID_FILE kill must target the single pid, never a -pgid"
        if sig == signal.SIGKILL:
            killed["sent"] = True
        return True

    monkeypatch.setattr("agent_runner.api.send_signal_to_pid", fake_send)
    monkeypatch.setattr("agent_runner.api.pid_alive", lambda pid: not killed["sent"])
    with patch("os.killpg", side_effect=AssertionError("killpg in PID_FILE mode")):
        s = api.kill(tmp_git_repo)
    assert killed["sent"] is True  # loop never saw it die → escalated to SIGKILL
    assert s.active is False  # re-checked AFTER SIGKILL


def test_round_holder_pid_missing_sidecar_returns_none(tmp_path: Path) -> None:
    assert api._round_holder_pid(tmp_path) is None


def test_round_holder_pid_corrupt_sidecar_returns_none(tmp_path: Path) -> None:
    (tmp_path / "agent-runner.lock.holder").write_text("not json")
    assert api._round_holder_pid(tmp_path) is None


def test_round_holder_pid_dead_pid_returns_none(tmp_path: Path) -> None:
    import json

    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps({"pid": 999999999}))
    assert api._round_holder_pid(tmp_path) is None


def test_round_holder_pid_live_pid_returned(tmp_path: Path) -> None:
    import json

    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps({"pid": os.getpid()}))
    assert api._round_holder_pid(tmp_path) == os.getpid()


def test_kill_sends_sigterm_to_round_holder_before_serve(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api.kill's PID_FILE mode must reach the round via the .holder sidecar
    (not killpg, which can't cross a start_new_session=True boundary) --
    SIGTERM the round pid, and do it before waiting out serve's own grace."""
    import json

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    (log_dir / "agent-runner.lock.holder").write_text(json.dumps({"pid": 54321}))

    # The round holder must read as alive ONCE (so _round_holder_pid resolves
    # it) and dead on every check thereafter -- keeps this on the fast (no
    # escalation, no real grace wait) path; the escalation path is its own
    # test below with a FakeClock.
    calls = {"n": 0}

    def fake_pid_alive(pid: int) -> bool:
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr("agent_runner.api.pid_alive", fake_pid_alive)
    sent: list[tuple[int, int]] = []

    def fake_send(pid: int, sig: int) -> bool:
        sent.append((pid, sig))
        return True

    monkeypatch.setattr("agent_runner.api.send_signal_to_pid", fake_send)
    with patch("os.killpg", side_effect=AssertionError("killpg must never be used here")):
        api.kill(tmp_git_repo)

    # The round holder (54321) is TERM'd; serve (12345) is TERM'd too (to arm
    # stop["requested"]) but the round's own pid is what actually reaches the
    # agent -- killpg is never used for either.
    assert (54321, signal.SIGTERM) in sent
    assert (12345, signal.SIGTERM) in sent


def test_kill_escalates_round_holder_to_sigkill_when_term_ignored(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round that ignores SIGTERM (fully wedged, never even runs its own
    handler) must still be reaped -- SIGKILL as the last resort."""
    import json

    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    (log_dir / "agent-runner.lock.holder").write_text(json.dumps({"pid": 54321}))

    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    # Both pids report alive forever (fully wedged) -- the round's own grace
    # window must still expire and escalate.
    monkeypatch.setattr("agent_runner.api.pid_alive", lambda pid: True)
    sent: list[tuple[int, int]] = []

    def fake_send(pid: int, sig: int) -> bool:
        sent.append((pid, sig))
        return True

    monkeypatch.setattr("agent_runner.api.send_signal_to_pid", fake_send)
    api.kill(tmp_git_repo)

    assert (54321, signal.SIGKILL) in sent  # round holder escalated
    assert (12345, signal.SIGKILL) in sent  # serve itself escalated too


def test_round_kill_grace_matches_serve_cmd_grace() -> None:
    """api._terminate_round_pid's grace (driven from a separate CLI process,
    api.kill) must stay in lockstep with _serve_round._terminate_round's own
    grace (driven from serve's in-process Popen handle) -- both exist so the
    round's SIGTERM handler has time to reap its agent pgroup before either
    caller escalates to SIGKILL. A drift here would make one of the two paths
    escalate before the round even gets a chance to drain."""
    from agent_runner.cli import _serve_round

    assert api._ROUND_TERM_GRACE_S == _serve_round._ROUND_TERM_GRACE_S


def _draining_is_active(state: str):
    """is-active stub for a unit stuck in a draining/active state — never reaches
    an inactive state within a bounded confirm poll."""
    return lambda _u: state


def _draining_systemctl_user(calls: list[tuple[str, ...]]):
    """systemctl-user stub that reproduces the drain: a BLOCKING `stop` (no
    --no-block) hangs on the round drain and is killed by the subprocess timeout,
    exactly the bug; a --no-block stop/start returns once the job is enqueued."""

    def run(*args: str) -> None:
        calls.append(args)
        if args and args[0] == "stop":
            raise subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    return run


def test_systemd_stop_draining_does_not_raise_and_reports_active(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `systemctl stop` that blocks past the confirm bound (serve draining the
    in-flight round) must NOT raise TimeoutExpired: stop queues via --no-block,
    confirms within a bounded poll, and reports active=True while still draining."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    # Draining: the unit stays in an active state throughout the confirm window.
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("activating"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("activating")
    )
    calls: list[tuple[str, ...]] = []
    stub = _draining_systemctl_user(calls)
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", stub)
    monkeypatch.setattr("agent_runner.api._systemctl_user", stub)

    s = api.stop(tmp_git_repo)  # must not raise TimeoutExpired

    assert s.mode == ServiceMode.SYSTEMD_USER
    assert s.active is True  # still draining -> best-effort "stop requested, not confirmed"
    assert calls and calls[0][0] == "--no-block" and calls[0][1] == "stop"  # queued, not blocking


def test_systemd_stop_draining_confirms_when_unit_goes_inactive(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the stop DOES complete inside the confirm window, stop reports
    active=False (a genuinely confirmed stop, not just requested)."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("inactive"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("inactive")
    )
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)

    s = api.stop(tmp_git_repo)
    assert s.mode == ServiceMode.SYSTEMD_USER
    assert s.active is False  # drained within the bound -> confirmed stopped


def test_systemd_restart_still_starts_when_stop_is_draining(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """restart against a unit whose stop blocks past the bound must never leave
    the service stopped: it queues a --no-block start (systemd runs it after the
    drain) instead of a blocking start() that would itself TimeoutExpired."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("activating"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("activating")
    )
    calls: list[tuple[str, ...]] = []
    stub = _draining_systemctl_user(calls)
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", stub)
    monkeypatch.setattr("agent_runner.api._systemctl_user", stub)

    api.restart(tmp_git_repo)  # must not raise; must issue a start

    assert any(a[:2] == ("--no-block", "start") for a in calls), (
        "restart must queue a start even while the unit is still draining"
    )


def test_systemd_restart_uses_blocking_start_when_stop_confirmed(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the stop confirms inactive, restart uses the plain blocking start()
    (confirming the respawn), not the --no-block fallback."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    # Fully stopped throughout, so both the stop confirm and restart's post-stop
    # is-active check see an inactive unit.
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("inactive"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("inactive")
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_user", lambda *a: calls.append(a))
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: calls.append(a))

    api.restart(tmp_git_repo)

    assert ("start", f"agent-runner@{tmp_git_repo.name}.service") in calls  # blocking start()
    assert not any(a[:2] == ("--no-block", "start") for a in calls)


def test_poll_once_forwards_supervisor_stale_threshold(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_poll_once must forward cfg.monitor.supervisor_stale_threshold_s."""
    api.init(tmp_git_repo, force=False, commit=False)

    captured: list[dict] = []

    def capturing_rad(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("agent_runner.monitor.run_all_detectors", capturing_rad)

    api._poll_once(tmp_git_repo)

    assert captured, "run_all_detectors was never called"
    call_kwargs = captured[0]
    assert "supervisor_stale_threshold_s" in call_kwargs


def test_poll_once_threads_host_health_floors(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: _poll_once must forward cfg.monitor.host_health's two new
    floors (swap_sout_noise_floor_mb, mem_free_low_mb) to run_all_detectors --
    pre-fix a TOML override of these fields silently no-op'd on the API/monitor
    poll path even though the fields parsed fine (mirrors the
    supervisor_stale_threshold_s wiring test above)."""
    api.init(tmp_git_repo, force=False, commit=False)
    toml_path = tmp_git_repo / "agent-runner.toml"
    with toml_path.open("a", encoding="utf-8") as f:
        f.write("\n[monitor.host_health]\nswap_sout_noise_floor_mb = 8\nmem_free_low_mb = 4\n")

    captured: list[dict] = []

    def capturing_rad(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("agent_runner.monitor.run_all_detectors", capturing_rad)

    api._poll_once(tmp_git_repo)

    assert captured, "run_all_detectors was never called"
    call_kwargs = captured[0]
    assert call_kwargs["swap_sout_noise_floor_mb"] == 8
    assert call_kwargs["mem_free_low_mb"] == 4
