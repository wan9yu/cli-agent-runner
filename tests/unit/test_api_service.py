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


def test_git_repo_should_return_init_result_when_api_init(tmp_git_repo: Path) -> None:
    result = api.init(tmp_git_repo, force=False, commit=False)

    assert isinstance(result, InitResult)
    assert result.work_dir == tmp_git_repo
    assert any(f.name == "agent-runner.toml" for f in result.files_created)


def test_no_systemd_no_pid_should_return_mode_none_when_api_status(tmp_git_repo: Path) -> None:
    api.init(tmp_git_repo, force=False, commit=False)

    s = api.status(tmp_git_repo)

    assert isinstance(s, ServiceStatus)
    assert s.mode == ServiceMode.NONE
    assert s.active is False


def test_pid_file_with_self_pid_should_be_active_when_status(
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


def test_pid_file_with_dead_pid_should_be_inactive_when_status(
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


def test_pid_file_should_send_sigterm_when_api_stop(
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


def test_pid_file_should_send_sigterm_then_sigkill_when_api_kill(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    # The pid stays alive through the whole SIGTERM grace window, so api.kill
    # escalates to SIGKILL -- the branch the name promises. Without this the pid
    # would report dead right after SIGTERM and SIGKILL would never be sent (nor
    # asserted). FakeClock lets the grace deadline expire with no real sleep.
    monkeypatch.setattr("agent_runner.api.pid_alive", lambda pid: True)
    sent: list[tuple[int, int]] = []

    def fake_send(pid: int, sig: int) -> bool:
        sent.append((pid, sig))
        return True

    monkeypatch.setattr("agent_runner.api.send_signal_to_pid", fake_send)

    api.kill(tmp_git_repo)

    assert (12345, signal.SIGTERM) in sent
    assert (12345, signal.SIGKILL) in sent
    assert sent.index((12345, signal.SIGTERM)) < sent.index((12345, signal.SIGKILL))


def test_install_with_no_systemctl_should_return_install_result_when_called(
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


def test_install_with_monitor_should_write_two_units_when_called(
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


def test_installed_unit_should_be_removed_when_uninstall(
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


def test_draining_unit_should_not_raise_when_uninstall(
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


def test_per_phase_override_should_be_forwarded_to_monitor_when_poll_once(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_systemd_failed_should_be_inactive_when_status(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: "failed")

    s = api.status(tmp_git_repo)

    assert s.mode == ServiceMode.SYSTEMD_USER
    assert s.active is False


def test_systemd_activating_should_be_active_when_status(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: "activating")

    s = api.status(tmp_git_repo)

    assert s.active is True


def test_systemctl_absent_should_fall_back_to_pid_when_status(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", lambda u: None)

    s = api.status(tmp_git_repo)

    assert s.active is True  # live serve.pid


def test_systemctl_is_active_seam_should_return_none_when_binary_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr("agent_runner.api.subprocess.run", boom)

    result = api._systemctl_is_active("agent-runner@x.service")

    assert result is None


def test_pid_file_should_refuse_before_stopping_when_restart(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain serve process can't be respawned by the CLI — restart must refuse
    FIRST, never leaving a half-executed stop-without-start."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")

    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        with pytest.raises(RuntimeError, match="systemd"):
            api.restart(tmp_git_repo)

    send.assert_not_called()  # refused BEFORE stop()


def test_system_unit_should_refuse_with_systemctl_command_when_restart(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `--system` install's unit is root-owned; restart can't touch it, so it
    must refuse naming the exact `sudo systemctl restart ...` remedy instead of
    falling through to the PID_FILE/NONE "start it by hand" message."""
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr("agent_runner.api._system_unit_exists", lambda project: True)

    with pytest.raises(RuntimeError, match="sudo systemctl restart"):
        api.restart(tmp_git_repo)


def test_system_unit_should_refuse_without_user_teardown_when_uninstall(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """uninstall against a `--system` install must not touch user-scope units at
    all -- it refuses and prints the `sudo systemctl disable --now ...` remedy."""
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr("agent_runner.api._system_unit_exists", lambda project: True)

    def _boom(*a, **k):
        raise AssertionError("user-scope teardown must not run for a system-managed unit")

    monkeypatch.setattr("agent_runner.lifecycle.stop_unit_draining", _boom)
    monkeypatch.setattr("agent_runner.api._systemctl_user", _boom)

    result = api.uninstall(tmp_git_repo)

    assert result is False
    assert "sudo systemctl disable --now" in capsys.readouterr().out


def test_system_unit_should_include_monitor_remedy_when_both_units_installed_and_uninstall(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A --system --monitor install writes BOTH a serve and a monitor unit
    under _SYSTEM_UNITS_DIR; the refusal message must name both remedies --
    naming only the serve unit would leave an orphaned, enabled, root-owned
    monitor unit behind if the operator follows the printed command verbatim."""
    api.init(tmp_git_repo, force=False, commit=False)
    fake_system = tmp_git_repo / "fake-system-systemd"
    fake_system.mkdir()
    project = tmp_git_repo.name
    serve_unit = f"agent-runner@{project}.service"
    monitor_unit = f"agent-runner-monitor@{project}.service"
    (fake_system / serve_unit).write_text("[Unit]\n")
    (fake_system / monitor_unit).write_text("[Unit]\n")
    monkeypatch.setattr("agent_runner.api._SYSTEM_UNITS_DIR", fake_system)

    result = api.uninstall(tmp_git_repo)

    assert result is False
    out = capsys.readouterr().out
    assert f"disable --now {serve_unit}" in out
    assert f"disable --now {monitor_unit}" in out


def test_no_user_bus_should_not_raise_when_uninstall(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-user-bus host (dietpi/RPi, exactly where --system is recommended) has
    no units under lifecycle._user_systemd_dir(), so the per-unit loop is a
    no-op -- but the final `daemon-reload` ran unconditionally and crashed with
    an uncaught CalledProcessError. It must now be swallowed."""
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr(api, "_system_unit_exists", lambda project: False)
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "no-such-systemd-dir"
    )

    def _boom(*args: str) -> None:
        raise subprocess.CalledProcessError(1, "systemctl")

    monkeypatch.setattr("agent_runner.api._systemctl_user", _boom)

    result = api.uninstall(tmp_git_repo)

    assert result is True


def test_system_unit_should_set_system_managed_when_status_and_pid_file_mode(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status() on a --system install falls through detect_service_mode into
    PID_FILE (no user unit exists) -- it must still surface who manages the
    service via `system_managed`, distinct from `unit_file` (which stays the
    live, user-owned, CLI-restartable systemd_user unit's path -- overloading
    it for a root-owned --system unit would misclassify it on the --json/
    plugin surface)."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    monkeypatch.setattr("agent_runner.api._system_unit_exists", lambda project: True)

    s = api.status(tmp_git_repo)

    assert s.mode == ServiceMode.PID_FILE
    assert s.system_managed is True
    assert s.unit_file is None


def test_pid_file_should_recheck_alive_after_sigkill_when_kill(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After SIGKILL, active must reflect the post-kill liveness, not the stale
    pre-SIGKILL True."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
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


def test_round_holder_pid_should_return_none_when_sidecar_missing(tmp_path: Path) -> None:
    result = api._round_holder_pid(tmp_path)

    assert result is None


def test_round_holder_pid_should_return_none_when_sidecar_corrupt(tmp_path: Path) -> None:
    (tmp_path / "agent-runner.lock.holder").write_text("not json")

    result = api._round_holder_pid(tmp_path)

    assert result is None


def test_round_holder_pid_should_return_none_when_pid_dead(tmp_path: Path) -> None:
    import json

    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps({"pid": 999999999}))

    result = api._round_holder_pid(tmp_path)

    assert result is None


def test_round_holder_pid_should_return_pid_when_pid_live(tmp_path: Path) -> None:
    import json

    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps({"pid": os.getpid()}))

    result = api._round_holder_pid(tmp_path)

    assert result == os.getpid()


def test_round_holder_pid_should_return_none_when_create_time_mismatches(
    tmp_path: Path,
) -> None:
    import json

    import psutil

    holder = {
        "pid": os.getpid(),
        "create_time": psutil.Process(os.getpid()).create_time() - 5000.0,
    }
    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps(holder))

    result = api._round_holder_pid(tmp_path)

    assert result is None


def test_round_holder_pid_should_return_pid_when_create_time_matches(tmp_path: Path) -> None:
    import json

    import psutil

    holder = {
        "pid": os.getpid(),
        "create_time": psutil.Process(os.getpid()).create_time(),
    }
    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps(holder))

    result = api._round_holder_pid(tmp_path)

    assert result == os.getpid()


def test_round_holder_pid_should_best_effort_return_pid_when_create_time_missing(
    tmp_path: Path,
) -> None:
    import json

    (tmp_path / "agent-runner.lock.holder").write_text(json.dumps({"pid": os.getpid()}))

    result = api._round_holder_pid(tmp_path)

    assert result == os.getpid()


def test_kill_should_send_sigterm_to_serve_before_round_holder(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api.kill's PID_FILE mode must reach the round via the .holder sidecar
    (not killpg, which can't cross a start_new_session=True boundary). Serve
    is SIGTERM'd FIRST (arming its own stop["requested"] handler before the
    in-flight round is force-ended), then the round holder -- both before
    serve's own grace-wait."""
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

    # Serve (12345) is TERM'd first (to arm stop["requested"]); the round
    # holder (54321) is TERM'd next -- the round's own pid is what actually
    # reaches the agent, killpg is never used for either.
    assert (54321, signal.SIGTERM) in sent
    assert (12345, signal.SIGTERM) in sent
    assert sent.index((12345, signal.SIGTERM)) < sent.index((54321, signal.SIGTERM))


def test_kill_should_escalate_round_holder_to_sigkill_when_term_ignored(
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


def test_round_kill_grace_should_match_serve_cmd_grace() -> None:
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


def test_systemd_stop_should_not_raise_and_report_active_when_draining(
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


def test_systemd_stop_draining_should_confirm_when_unit_goes_inactive(
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


def test_systemd_restart_should_still_start_when_stop_is_draining(
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


def test_systemd_restart_should_use_blocking_start_when_stop_confirmed(
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


def test_kill_systemd_should_escalate_to_sigkill_when_still_active(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill's SYSTEMD_USER branch must mirror the PID_FILE branch: SIGTERM
    first, then -- if the unit is still active after the grace window --
    escalate to SIGKILL. `systemctl kill` queues no stop job, so
    TimeoutStopSec never applies; without this the systemd path would never
    reap a wedged unit, unlike PID_FILE kill()."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    # Never goes inactive -- both the pre-grace and post-grace check see it.
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("active"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("active")
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: calls.append(a))

    s = api.kill(tmp_git_repo)

    unit = f"agent-runner@{tmp_git_repo.name}.service"
    assert ("kill", "--signal=SIGTERM", unit) in calls
    assert ("kill", "--signal=SIGKILL", unit) in calls  # escalation happened
    assert s.mode == ServiceMode.SYSTEMD_USER


def test_kill_systemd_should_not_escalate_when_sigterm_already_stopped_it(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror case: when the unit is already inactive after SIGTERM (no
    wedge), kill must NOT send a redundant SIGKILL -- escalation is only for
    a unit still active past the grace window."""
    api.init(tmp_git_repo, force=False, commit=False)
    _fake_systemd_unit(tmp_git_repo, monkeypatch)
    monkeypatch.setattr("agent_runner.api.SYSTEM_CLOCK", FakeClock())
    monkeypatch.setattr("agent_runner.api._systemctl_is_active", _draining_is_active("inactive"))
    monkeypatch.setattr(
        "agent_runner.lifecycle._systemctl_is_active", _draining_is_active("inactive")
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: calls.append(a))

    s = api.kill(tmp_git_repo)

    unit = f"agent-runner@{tmp_git_repo.name}.service"
    assert ("kill", "--signal=SIGTERM", unit) in calls
    assert ("kill", "--signal=SIGKILL", unit) not in calls  # already stopped -- no escalation
    assert s.mode == ServiceMode.SYSTEMD_USER


def test_poll_once_should_forward_supervisor_stale_threshold(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_poll_once_should_thread_host_health_floors(
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
