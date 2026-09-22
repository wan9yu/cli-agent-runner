"""Mid-round memory.high soft-brake on _spawn_round (warning-streak path)."""

from __future__ import annotations

import dataclasses
import sys

import pytest

from agent_runner.cli import serve_cmd
from agent_runner.config import (
    MonitorHostHealthConfig,
    _HostHealthBrakeConfig,
)
from tests._clock import FakeClock, install_scripted_round
from tests._test_helpers import read_events_for_current_month
from tests.integration.test_spawn_round_mem_floor import (
    _CRITICAL_SAMPLE,
    _DUMMY_ARGV,
    _HEALTHY_SAMPLE,
)

_WARNING_SAMPLE = {
    "psi_some_avg10": 10.0,  # >= some_avg10_warning (5.0), < full_avg10_critical (60.0)
    "psi_full_avg10": 10.0,
    "mem_free_mb": 4000,
    "mem_available_mb": 4000,
    "swap_sout": 0,
}


def test_spawn_round_should_engage_and_restore_soft_brake_when_warning_sustained_and_armed(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=3)
    calls = {"engage": 0, "restore": []}

    def _fake_engage(*, step_pct):
        calls["engage"] += 1
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    assert calls["engage"] == 1
    assert calls["restore"] == ["max"]  # finally restored the stashed token
    events = read_events_for_current_month(log_dir)
    engaged = [e for e in events if e.get("event") == "memory_high_engaged"]
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert len(engaged) == 1 and engaged[0]["written"] == 999
    assert released and released[0]["reason"] == "round_end"


def test_spawn_round_should_engage_else_crash_when_warning_threshold_is_zero_and_pressure_absent(
    tmp_path, monkeypatch
):
    """Review Minor 1 regression: warning_streak resets to 0 on a no-pressure
    tick, so `warning_streak >= warning_consecutive_samples` is trivially true
    every tick when a directly-constructed config (bypassing the TOML parser's
    _require_positive_int validation) sets warning_consecutive_samples=0. Without
    the engage guard's own `pressure is not None` conjunct, this would engage
    the brake on a HEALTHY sample and then crash reading None.signal/.context --
    a fail-CLOSED crash on the reap-adjacent path. Must neither engage nor crash."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=3)
    argv = _DUMMY_ARGV
    calls = {"engage": 0}

    def _fake_engage(*, step_pct):
        calls["engage"] += 1
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=0)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        return _HEALTHY_SAMPLE  # memory_pressure() returns None for this sample

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=_sample_fn,
    )

    assert rc == 0  # completed cleanly -- no AttributeError crash
    assert calls["engage"] == 0  # never engaged on a no-pressure tick


def test_spawn_round_should_restore_soft_brake_when_terminated_by_sustained_critical_pressure(
    tmp_path, monkeypatch
):
    """Review Minor 3 coverage: the "terminate"-verdict return is a SEPARATE
    exit point inside the try from the clean-exit return already covered above
    -- prove the finally's restore runs there too."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=None)
    argv = _DUMMY_ARGV
    calls = {"restore": []}

    def _fake_engage(*, step_pct):
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        # 3 warning ticks engage the brake, then sustained critical pressure
        # (default critical_consecutive_samples=3) hard-terminates the round.
        return _WARNING_SAMPLE if calls_n["n"] <= 3 else _CRITICAL_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=_sample_fn,
    )

    assert rc != 0  # terminated, not a clean exit
    assert calls["restore"] == ["max"]
    events = read_events_for_current_month(log_dir)
    assert len([e for e in events if e.get("event") == "round_mem_terminated"]) == 1
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert released and released[0]["reason"] == "round_end"


def test_spawn_round_should_restore_brake_and_propagate_original_exception_when_body_raises(
    tmp_path, monkeypatch
):
    """Review Minor 2 + Minor 3 coverage together: the except-BaseException
    unwind path also restores the brake via the finally, AND a shielded emit
    failure during that cleanup must never replace the ORIGINAL propagating
    exception with an emit error."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=20)
    argv = _DUMMY_ARGV
    calls = {"restore": []}

    def _fake_engage(*, step_pct):
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    def _raise_on_release(*_a, **_k):
        raise OSError("disk full during emit -- must not mask the original exception")

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    monkeypatch.setattr(_serve_cgroup, "emit_memory_high_released", _raise_on_release)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=1)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        if calls_n["n"] >= 2:
            raise RuntimeError("the original round-body exception")
        return _WARNING_SAMPLE  # tick 1: engages the brake (threshold=1)

    with pytest.raises(RuntimeError):
        serve_cmd._spawn_round(
            argv,
            log_dir / "round-1.log",
            {},
            timeout_s=300,
            round_num=1,
            host_health_cfg=hh,
            clock=clock,
            sample_fn=_sample_fn,
        )

    assert calls["restore"] == ["max"]  # cleanup still ran despite the shielded emit raising


def test_spawn_round_should_emit_write_failed_when_restore_itself_fails(tmp_path, monkeypatch):
    """Review Minor 3 coverage, updated for fix-wave Minor 4: restore_leaf_memory_high
    returning False (a restore-time OSError, fail-open) must emit memory_high_write_failed
    with errno=None -- never the misleading errno=0 (reads as POSIX success), since the
    finally has no real errno to report (restore_leaf_memory_high swallows it into a bare
    bool) -- and never memory_high_released."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=3)

    def _fake_engage(*, step_pct):
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        return False  # simulates a restore-time OSError, fail-open

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "memory_high_released"] == []
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert failed and failed[0]["errno"] is None


def test_spawn_round_should_carry_the_real_errno_when_engage_itself_fails(tmp_path, monkeypatch):
    """Fix-wave Minor 4 counterpart: the engage-fail site (a real OSError from
    metrics.engage_leaf_memory_high's write) must keep forwarding the real
    errno unchanged -- only the restore-fail sites (which never see a real
    errno) switched to the None sentinel."""
    import errno as errno_mod

    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=3)

    def _fake_engage(*, step_pct):
        return {"engaged": False, "errno": errno_mod.EACCES}

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    events = read_events_for_current_month(log_dir)
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert failed and failed[0]["errno"] == errno_mod.EACCES


def test_spawn_round_should_latch_recovery_restore_failure_flooding_each_healthy_tick_when_invoked(
    tmp_path, monkeypatch
):
    """Fix-wave Minor 2: when the recovery-path restore keeps failing, the
    engage path already had brake_disarmed to stop retry-and-re-emit every
    tick; the recovery path had no equivalent, so none_streak kept growing
    past the threshold and memory_high_write_failed re-fired on EVERY
    subsequent healthy tick (~one per 10s) for the rest of the round -- a
    flood. brake_restore_failed latches after the first recovery-path
    failure. The finally at round end is a separate, distinct restore
    attempt and may add exactly one more emission -- that one is NOT part
    of the flood this fix removes."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=9)
    seq = {"n": 0}
    restore_calls = {"n": 0}

    def _sample():
        seq["n"] += 1
        if seq["n"] <= 3:
            return _WARNING_SAMPLE  # 3 warning ticks -> engage
        return _HEALTHY_SAMPLE

    def _fake_restore(previous, **_k):
        restore_calls["n"] += 1
        return False  # a persistently-failing restore, fail-open

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        # This test runs the most mid-round ticks in the file (engage at 1-3,
        # then healthy 4..9), so FakeClock elapsed time climbs
        # high; 3000s (matching the 11-tick test above) keeps the round ceiling
        # well clear of it, so a slow CI child's exit is always the round's end,
        # never a spurious timeout SIGTERM (the py3.11/macOS flake).
        timeout_s=3000,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=_sample,
    )

    assert rc == 0
    # exactly 2 restore attempts: the ONE latched recovery attempt (tick 6, when
    # none_streak first reaches the threshold) + the finally's own attempt at
    # round end -- never one per healthy tick (6 healthy ticks ran: 4..9).
    assert restore_calls["n"] == 2
    events = read_events_for_current_month(log_dir)
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert len(failed) == 2
    assert all(e["errno"] is None for e in failed)


def test_spawn_round_should_release_soft_brake_when_warning_clears_for_n_ticks(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=6)
    seq = {"n": 0}
    restores = []

    def _sample():
        seq["n"] += 1
        return _WARNING_SAMPLE if seq["n"] <= 3 else _HEALTHY_SAMPLE  # 3 warn engage, then heal

    def _fake_restore(previous, **_k):
        restores.append(previous)
        return True

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=_sample,
    )

    events = read_events_for_current_month(log_dir)
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert any(e["reason"] == "recovered" for e in released)


def test_spawn_round_should_hold_soft_brake_when_a_critical_sample_follows_engage(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=4)
    seq = {"n": 0}

    def _sample():
        seq["n"] += 1
        if seq["n"] <= 3:
            return _WARNING_SAMPLE  # engage
        if seq["n"] == 4:
            return _CRITICAL_SAMPLE  # a critical sample must NOT release the brake
        return _CRITICAL_SAMPLE

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", lambda p, **_k: True)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    serve_cmd._spawn_round(
        _DUMMY_ARGV,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=clock,
        sample_fn=_sample,
    )

    events = read_events_for_current_month(log_dir)
    released = [e for e in events if e.get("event") == "memory_high_released"]
    # NO "recovered" -- held through critical
    assert [e["reason"] for e in released] == ["round_end"]


def test_spawn_round_should_engage_brake_then_terminate_when_warning_precedes_sustained_critical(
    tmp_path, monkeypatch
):
    """D-1 tests-of-record for the brake-engage -> terminate ORDER: the soft
    brake (memory.high) engages first on sustained WARNING pressure, and if
    pressure keeps worsening to sustained CRITICAL the hard floor still
    terminates the round on top of it -- the brake is a softer, earlier
    action, never a substitute for the terminate floor. Both halves are
    asserted so this can never silently pass as a no-op (e.g. a future
    change that made the brake swallow the pressure and skip the terminate
    check would fail `rc != 0` here).

    `defer_to_cgroup` is explicitly False -- a fully-bounded cgroup would
    correctly defer to the kernel instead (see
    test_spawn_round_should_defer_to_cgroup_when_memory_and_swap_both_bounded),
    which would make the terminate half unreachable and this test vacuous.

    Arming (`_BRAKE_ARMED_BY_LOG_DIR`) happens at serve-boot in
    `_probe_and_emit_cgroup_defer` in production, not inside `_spawn_round`
    itself, so this test sets it directly the way that boot step would have
    -- and resets it afterward, since it is global mutable state keyed by a
    tmp_path this process will not reuse, but must not leak into any other
    test that happens to construct the same log_dir."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock, exit_after_timeouts=None)
    argv = _DUMMY_ARGV
    calls = {"engage": 0, "restore": []}

    def _fake_engage(*, step_pct):
        calls["engage"] += 1
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    # Frozen dataclasses: must CONSTRUCT the brake config, never mutate a
    # field of an existing instance in place (that raises FrozenInstanceError).
    hh = dataclasses.replace(
        MonitorHostHealthConfig(),
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3),
    )
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True
    try:
        calls_n = {"n": 0}

        def _sample_fn():
            calls_n["n"] += 1
            # 3 sustained warning ticks engage the brake; pressure then
            # worsens to sustained critical, which must still terminate.
            return _WARNING_SAMPLE if calls_n["n"] <= 3 else _CRITICAL_SAMPLE

        rc = serve_cmd._spawn_round(
            argv,
            log_dir / "round-1.log",
            {},
            timeout_s=300,
            round_num=1,
            host_health_cfg=hh,
            clock=clock,
            sample_fn=_sample_fn,
            defer_to_cgroup=False,
        )

        assert calls["engage"] == 1  # the brake engaged -- not a no-op
        assert rc != 0  # ...and the round still terminated, not a clean exit

        events = read_events_for_current_month(log_dir)
        kinds = [e.get("event") for e in events]
        assert kinds.count("memory_high_engaged") == 1
        assert kinds.count("round_mem_terminated") == 1
        assert calls["restore"] == ["max"]  # the finally restored the brake on the terminate exit
    finally:
        del _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir]


def test_spawn_round_should_skip_sampling_when_host_health_cfg_is_none(tmp_path):
    """host_health_cfg defaults to None: existing callers (no mid-round floor
    wired) get byte-identical behavior -- the sampler is never even invoked."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "print('ok')"]
    calls = []

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        sample_fn=lambda: calls.append(1) or _CRITICAL_SAMPLE,
    )

    assert rc == 0
    assert calls == []
