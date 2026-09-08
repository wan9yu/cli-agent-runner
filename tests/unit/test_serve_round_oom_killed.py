"""round_oom_killed: pointer-only event when the kernel cgroup-OOM-killed a
round (``memory.events.oom_kill`` rose over the round), folded from
``_emit_round_cgroup_memory``'s SAME ``cgroup_memory_usage()`` read -- no
second sysfs read. Symmetric with ``round_mem_terminated`` (supervisor-
killed); the classification (crash streak / give-up decision) is UNCHANGED --
this is pure observability layered on top."""

from __future__ import annotations

import glob
import json
import types

from agent_runner.cli import _serve_round


def _events(log_dir):
    return [
        json.loads(line)
        for f in sorted(glob.glob(str(log_dir / "events-*.jsonl")))
        for line in open(f)
    ]


def _cgroup_state(oom_kill: int, path: str = "/user.slice") -> dict:
    return {
        "baseline_events": {"high": 0, "max": 0, "oom": 0, "oom_kill": oom_kill},
        "peak_current": 320_000_000,
        "peak_swap": 160_000_000,
        "bounding_cgroup_path": path,
    }


def _usage(oom_kill: int, oom: int = 0) -> dict:
    return {
        "memory_events": {"high": 0, "max": 0, "oom": oom, "oom_kill": oom_kill},
        "memory_current": 0,
        "memory_swap_current": 0,
        "cgroup_path": "/user.slice",
    }


def test_oom_kill_delta_emits_pointer_only_event(tmp_path, monkeypatch):
    log = tmp_path / "round-9.log"
    log.write_text("some transcript bytes")
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[tmp_path] = _cgroup_state(oom_kill=3)
    monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: _usage(4, oom=1))

    cur, baseline = _serve_round._emit_round_cgroup_memory(tmp_path, log, 9)
    # 137 == _ROUND_UNREAPED_RC: this round actually died to the kill, so the
    # delta is attributable to it (not a sibling under the same ancestor).
    _serve_round._maybe_emit_oom_killed(tmp_path, log, 9, cur, 137, baseline)

    evs = _events(tmp_path)
    ooms = [e for e in evs if e["event"] == "round_oom_killed"]
    assert len(ooms) == 1  # exactly once
    oom = ooms[0]
    assert oom["oom_kill_delta"] == 1  # 4 - 3
    assert oom["log_path"].endswith("round-9.log")
    assert oom["log_bytes"] == len("some transcript bytes")
    assert "transcript" not in json.dumps(oom)  # pointer-only, never content
    assert oom["partial_log"] is True

    # supervisor-owned marker actually written -- a residue pointer target,
    # never event content.
    assert "[agent-runner] round log truncated: cgroup OOM-kill" in log.read_text()


def test_sibling_oom_kill_with_clean_exit_does_not_emit(tmp_path, monkeypatch):
    """The bounding cgroup can be a shared ANCESTOR slice: a rising oom_kill
    counter can be a SIBLING process's kill, not this round's. A clean-exit
    round (returncode 0) must not be misattributed -- no round_oom_killed,
    and this round's INTACT log must not be marked partial."""
    log = tmp_path / "round-9.log"
    log.write_text("this round exited cleanly")
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[tmp_path] = _cgroup_state(oom_kill=3)
    monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: _usage(4))

    cur, baseline = _serve_round._emit_round_cgroup_memory(tmp_path, log, 9)
    # clean exit, not a kill
    _serve_round._maybe_emit_oom_killed(tmp_path, log, 9, cur, 0, baseline)

    assert [e for e in _events(tmp_path) if e["event"] == "round_oom_killed"] == []
    assert log.read_text() == "this round exited cleanly"  # no trailer appended


def test_normal_round_does_not_emit_oom_killed(tmp_path, monkeypatch):
    """oom_kill counter unchanged over the round -- no event, no marker."""
    log = tmp_path / "round-1.log"
    log.write_text("clean round output")
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[tmp_path] = _cgroup_state(oom_kill=2)
    monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: _usage(2))

    cur, baseline = _serve_round._emit_round_cgroup_memory(tmp_path, log, 1)
    _serve_round._maybe_emit_oom_killed(tmp_path, log, 1, cur, 0, baseline)

    assert [e for e in _events(tmp_path) if e["event"] == "round_oom_killed"] == []
    assert log.read_text() == "clean round output"  # no trailer appended


def test_no_finite_cgroup_bound_does_not_emit_oom_killed(tmp_path):
    """Host has no finite cgroup bound -- _emit_round_cgroup_memory no-ops
    ({}), so there is nothing to diff and _maybe_emit_oom_killed must no-op."""
    log = tmp_path / "round-1.log"
    log.write_text("no cgroup on this host")

    cur, baseline = _serve_round._emit_round_cgroup_memory(tmp_path, log, 1)
    _serve_round._maybe_emit_oom_killed(tmp_path, log, 1, cur, 0, baseline)

    assert [e for e in _events(tmp_path) if e["event"] == "round_oom_killed"] == []


def test_supervisor_mem_terminated_round_does_not_also_emit_oom_killed(tmp_path, monkeypatch):
    """A round the SUPERVISOR's own host_health floor kills (round_mem_terminated)
    is a DISTINCT cause from the kernel's cgroup-OOM: the kernel never actually
    OOM-killed anything, so oom_kill does not rise and round_oom_killed must not
    also fire alongside it."""
    from agent_runner.api import emit_round_mem_terminated

    log = tmp_path / "round-1.log"
    log.write_text("terminated by the supervisor's mem-pressure floor")
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[tmp_path] = _cgroup_state(oom_kill=1)
    monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: _usage(1))

    emit_round_mem_terminated(
        tmp_path,
        pid=4242,
        severity="critical",
        signal="SIGTERM",
        message="host memory pressure critical",
        consecutive=3,
        context={},
    )
    cur, baseline = _serve_round._emit_round_cgroup_memory(tmp_path, log, 1)
    # -15: reaped SIGTERM (the supervisor's own signal, not a kernel SIGKILL) --
    # not a "round_was_killed" returncode either, so this exercises both guards.
    _serve_round._maybe_emit_oom_killed(tmp_path, log, 1, cur, -15, baseline)

    evs = _events(tmp_path)
    assert any(e["event"] == "round_mem_terminated" for e in evs)
    assert not any(e["event"] == "round_oom_killed" for e in evs)


def test_give_up_classification_unchanged_when_oom_killed_fires(tmp_path, monkeypatch):
    """round_oom_killed is pure observability bolted onto post_round_verdicts
    AFTER its give-up decisions are already computed: the returned
    (exit_code, delay, streaks) for a kernel-SIGKILL round (exit 137) must be
    byte-identical whether or not the cgroup scan detects an oom_kill rise."""
    from agent_runner._round_outcome import RoundOutcome

    outcome = RoundOutcome(
        mem_terminated=False,
        usage_capable=True,
        newest_usage_ts=None,
        newest_substrate_before_ts=None,
        latest_transient_per_agent={},
    )
    cfg = types.SimpleNamespace(runtime=types.SimpleNamespace(restart_delay_s=3))

    def _run(log_dir, oom_kill_baseline, oom_kill_now):
        log_dir.mkdir()
        log_path = log_dir / "round-1.log"
        log_path.write_text("agent output before the kill")
        if oom_kill_baseline is not None:
            _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = _cgroup_state(
                oom_kill=oom_kill_baseline
            )
            monkeypatch.setattr(
                _serve_round.metrics, "cgroup_memory_usage", lambda **k: _usage(oom_kill_now)
            )
        else:
            monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: {})
        return _serve_round.post_round_verdicts(
            cfg,
            log_dir=log_dir,
            round_log_path=log_path,
            round_num=1,
            r_returncode=137,
            round_duration_s=5.0,
            round_throttle_active=False,
            outcome=outcome,
            consecutive_crashes=0,
            consecutive_mem_terminations=0,
            consecutive_no_progress=0,
        )

    # A: kernel OOM-killed -- round_oom_killed fires alongside the verdict.
    log_dir_a = tmp_path / "a"
    result_a = _run(log_dir_a, oom_kill_baseline=0, oom_kill_now=1)
    assert any(e["event"] == "round_oom_killed" for e in _events(log_dir_a))

    # B: identical round, but no finite cgroup bound at all -- round_oom_killed
    # cannot fire (nothing to read).
    log_dir_b = tmp_path / "b"
    result_b = _run(log_dir_b, oom_kill_baseline=None, oom_kill_now=None)
    assert not any(e["event"] == "round_oom_killed" for e in _events(log_dir_b))

    assert result_a == result_b  # give-up decision/exit-code/streaks unchanged
