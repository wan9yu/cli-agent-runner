"""Tests for max_grace_after_result_s HUNG defense (0.1.31+)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from agent_runner import agent_runtime
from agent_runner.agent_runtime import run
from tests._test_helpers import poll_until, wait_for


def _write_fake_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def _wait_for_children(p, predicate, *, ignore_patterns=None, timeout_s=5.0):
    """Poll ``_live_children(p)`` until ``predicate(live, ignored)`` is true.

    Replaces a fixed ``time.sleep(0.5)`` that raced the backgrounded child's
    fork under load: a busy full-suite run could see the sleep elapse before
    the child had actually forked, making the assertion flake (or, worse,
    silently see an empty child list). Bounded (never hangs); asserts loudly
    on timeout instead of proceeding with a stale/empty result.
    """
    from agent_runner.agent_runtime import _live_children

    box: dict[str, list] = {}

    def _ready() -> bool:
        box["live"], box["ignored"] = _live_children(p, ignore_patterns=ignore_patterns)
        return predicate(box["live"], box["ignored"])

    assert poll_until(_ready, timeout_s=timeout_s), "backgrounded child never appeared"
    return box["live"], box["ignored"]


def test_grace_kill_fires_when_result_then_idle(tmp_path, monkeypatch):
    """Agent writes type=result then becomes a childless sleeper (exec) -> no
    live workers -> reaped within grace + tick latency.

    The exec'd sleep must outlast any realistic grace-detection delay (30s,
    not a finite few seconds): under real `-n auto` contention on a busy
    host, measured scan+grace latency occasionally stretched past 6s (CPU
    scheduling jitter competing with 8-10 other workers, not agent_runtime
    logic) -- a short finite sleep would let the child exit ON ITS OWN before
    the grace kill fires, silently turning this into a no-op that never
    proves escalation happened. timeout_s/the duration bound are widened to
    match that same measured contention headroom -- reproduced under >=2
    concurrent gates (~2-3x CPU oversubscription), 25/20 was occasionally
    breached, so both are widened further (45/40) for real margin."""
    monkeypatch.setattr(agent_runtime, "_RESULT_SCAN_INTERVAL_S", 0.1)
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result","is_error":false}\'\nexec sleep 30\n',
    )
    log_path = tmp_path / "round.log"
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=45,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert result.killed_for_grace is True
    assert result.duration_s < 40


def test_no_grace_kill_when_disabled(tmp_path):
    """max_grace=0 -> grace logic disabled; wall timeout governs."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result","is_error":false}\'\nsleep 5\n',
    )
    log_path = tmp_path / "round.log"
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,  # short wall timeout
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=0,
    )
    assert result.killed_for_grace is False
    assert result.timed_out is True  # killed by wall timeout instead


def test_no_grace_kill_when_result_not_emitted(tmp_path, monkeypatch):
    """No result event -> grace countdown never starts.

    timeout_s=40 (widened from 15, 0.2.19): a trivial "echo; exit 0" child
    can be starved past a tight wall under >=2 concurrent gates and get
    SIGTERMed before it ever runs -- see
    test_given_prompt_arg_template_when_run_then_prompt_substituted_in_argv
    in test_agent_runtime.py for the same mechanism, reproduced.
    """
    monkeypatch.setattr(agent_runtime, "_RESULT_SCAN_INTERVAL_S", 0.1)
    script = _write_fake_script(tmp_path, 'echo "no result here"\nexit 0\n')
    log_path = tmp_path / "round.log"
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=40,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert result.killed_for_grace is False
    assert result.timed_out is False
    assert result.exit_code == 0


def test_live_children_empty_when_no_children():
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["sleep", "3"], start_new_session=True)
    try:
        assert _live_children(p) == ([], [])
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_lists_backgrounded_child():
    p = subprocess.Popen(["bash", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        # Predicate waits for the child to actually be named "sleep", not just
        # "any live child exists": under heavy `-n auto`/host contention, bash's
        # fork() of the backgrounded job can be observed by psutil BEFORE its
        # exec() into /bin/sleep lands, briefly showing as a live child still
        # named "bash" -- `bool(live)` alone would satisfy the predicate on
        # that transient pre-exec sighting and go on to fail the name assert
        # below (confirmed by reproduction under heavy contention).
        live, ignored = _wait_for_children(
            p, lambda live, _ignored: any(c["name"] == "sleep" for c in live)
        )
        assert any(c["name"] == "sleep" for c in live)
        assert ignored == []
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_empty_when_process_gone():
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["true"])
    p.wait()
    assert _live_children(p) == ([], [])  # NoSuchProcess swallowed


def test_grace_extended_when_result_but_child_running(tmp_path, monkeypatch):
    """Agent emits result then backgrounds a long child -> live worker -> NOT
    grace-killed; round_timeout_s (wall) reaps it instead; extended fired once.

    timeout_s must give the extend-check at least one real chance to run
    before the wall clock fires: measured under `-n auto` contention on a
    busy host, the scan+grace check (even with the 0.1s patched interval)
    was occasionally starved of CPU for several seconds by 8-10 competing
    workers, so a too-tight timeout_s can race the round's own wall-clock
    kill BEFORE any extend check ever executes -- turning `len(extended)==1`
    into a coin flip rather than a reliable assertion."""
    monkeypatch.setattr(agent_runtime, "_RESULT_SCAN_INTERVAL_S", 0.1)
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nsleep 30 &\nwait\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=20,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
    )
    assert result.killed_for_grace is False  # spared by liveness
    assert result.timed_out is True  # round_timeout_s backstop reaped it
    assert len(extended) == 1  # emitted once, not per-tick
    live, ignored = extended[0]
    assert any(c["name"] == "sleep" for c in live)


@pytest.mark.serial
def test_grace_kill_after_child_exits_then_idle(tmp_path, monkeypatch):
    """Live child first (extend), child exits, agent becomes childless (exec)
    -> next tick reaps via grace (well before wall timeout).

    The child's lifetime must clear (result-detection latency, up to the
    patched 0.1s _RESULT_SCAN_INTERVAL_S) + max_grace_after_result_s(1s) with
    REAL margin, so the "still busy -> extend" observation isn't a race
    against the child's own exit. 10s gives that margin under measured
    `-n auto` contention on a busy host (the scan+grace check was
    occasionally starved of CPU for several seconds by 8-10 competing
    workers) -- a shorter child lifetime (e.g. 2s) let the child exit before
    the (contention-delayed) extend check ever ran, dropping `extended` to
    empty.

    Marked serial (0.2.19): reproduced under >=2 concurrent gates, the
    scan+grace check was starved badly enough that the 25s wall won outright
    (timed_out=True, killed_for_grace=False at 25.1s) or the duration bound
    was breached (21.7s). This is the one genuinely CPU/time-heavy test in
    this file (a real 10s child, not an immediate exec) -- serializing it
    removes it from self-competing against the rest of the real-subprocess
    timing family within the same gate, and the wall/bound below are also
    widened for real margin on top of that."""
    monkeypatch.setattr(agent_runtime, "_RESULT_SCAN_INTERVAL_S", 0.1)
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nsleep 10 &\nwait\nexec sleep 30\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=45,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
    )
    assert result.killed_for_grace is True  # reaped after child exited
    assert result.duration_s < 40  # ~10s child + reap, well under the 45s wall timeout
    assert len(extended) == 1


def test_live_children_splits_on_ignore_pattern():
    """A child whose cmdline matches an ignore pattern goes to 'ignored', others to 'live'."""
    # Use exec -a to rename a child's argv[0] to a matchable name. Matching
    # runs against the full cmdline, so the rename alone is enough for that;
    # the STORED "name" field no longer echoes argv[0] (see
    # test_live_children_name_ignores_argv0_rewrite) -- both children exec
    # into the same "sleep" binary, so their stored names are indistinguishable
    # and only "matched" tells them apart.
    p = subprocess.Popen(
        ["bash", "-c", "exec -a snapshot-bash-xyz sleep 30 & sleep 30 & wait"],
        start_new_session=True,
    )
    try:
        # Both children fork off in the same shell statement, but wait until
        # BOTH are visible AND correctly classified before asserting --
        # returning as soon as the first one appears could catch the
        # ignored/live split mid-populate. A raw count is not enough on its
        # own: under heavy `-n auto`/host contention, a freshly forked child
        # can be observed by psutil BEFORE its exec() lands, and pre-exec its
        # cmdline is still the parent's full script text (which itself
        # contains "snapshot-bash-"), so a transient sighting can put BOTH
        # children in `ignored` -- confirmed by reproduction. Requiring at
        # least one of EACH bucket only becomes true once exec() has landed
        # for both and they've settled into their real, differentiated
        # cmdlines.
        live, ignored = _wait_for_children(
            p,
            lambda live, ignored: bool(live) and bool(ignored),
            ignore_patterns=[re.compile(r"snapshot-bash-")],
        )
        # One child should match the ignore pattern; the plain sleep goes to live.
        assert len(ignored) == 1 and ignored[0]["matched"] == "snapshot-bash-"
        assert len(live) == 1 and live[0]["name"] == "sleep"
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_no_patterns_preserves_0138_behavior():
    """ignore_patterns=None -> tuple shape, but everything alive goes to 'live'."""
    p = subprocess.Popen(["bash", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        # See test_live_children_lists_backgrounded_child: wait for the actual
        # "sleep"-named child, not just "any live child" (a pre-exec "bash"
        # sighting would satisfy the latter under heavy contention).
        live, ignored = _wait_for_children(
            p, lambda live, _ignored: any(c["name"] == "sleep" for c in live)
        )
        assert ignored == []
        assert any(c["name"] == "sleep" for c in live)
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_grace_kill_fires_when_only_ignored_helper_alive(tmp_path, monkeypatch):
    """The 0.1.38 'persistent-helper caveat' fix: with a matching pattern, a round
    whose only live descendant is the ignored helper is reaped at grace, not
    deferred to round_timeout_s.

    timeout_s/the duration bound are widened for measured `-n auto`
    contention headroom (see test_grace_kill_fires_when_result_then_idle);
    the exec'd sleep is already 30s (non-finite), so only the wall-clock
    ceiling needed adjusting. Widened further (45/40) under >=2 concurrent
    gates, matching test_grace_kill_fires_when_result_then_idle."""
    monkeypatch.setattr(agent_runtime, "_RESULT_SCAN_INTERVAL_S", 0.1)
    # Fake agent: emit type=result, then exec into a 'helper' (no children remain).
    # The exec replaces the agent process itself (not a child); psutil.children()
    # gives descendants only — so after exec the agent has NO children -> reap.
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nexec -a snapshot-bash-test sleep 30\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=45,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
        grace_kill_ignore_patterns=[re.compile(r"snapshot-bash-")],
    )
    assert result.killed_for_grace is True
    assert result.duration_s < 40
    assert extended == []  # no extension emitted; reaped directly


def test_live_children_stores_no_argv_secret():
    """A child invoked with a secret CLI argument (the realistic leak shape --
    ``--api-key sk-...``, ``PGPASSWORD=...``) -> stored dict carries only
    name+pid; the secret string never appears.

    The secret must live in a real ARGUMENT (argv[1:]), not argv[0]: bash's
    ``exec -a NAME cmd`` sets argv[0] verbatim, and ``_live_children`` derives
    the stored "name" via ``Path(argv[0]).name`` -- which only strips a
    leading directory component. A slash-free argv[0] passes through
    unchanged, so putting the secret THERE would test a different (renamed
    argv[0]) edge case, not the "arguments leak, we only store the basename"
    property this test documents. A plain python child keeps the secret in
    argv[3:], exactly where a real subprocess invocation would put it."""
    p = subprocess.Popen(
        [
            "bash",
            "-c",
            f'"{sys.executable}" -c "import time; time.sleep(30)" '
            "--api-key sk-ant-SECRET123 x &\nwait\n",
        ],
        start_new_session=True,
    )
    try:
        live, ignored = _wait_for_children(p, lambda live, _ignored: bool(live))
        assert live, "the backgrounded child never appeared -- nothing was actually checked"
        blob = repr(live + ignored)
        assert "sk-ant-SECRET123" not in blob and "--api-key" not in blob
        assert all(set(c) <= {"name", "pid", "matched"} for c in live + ignored)
    finally:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        p.wait()


def test_live_children_name_ignores_argv0_rewrite():
    """A child that rewrites its OWN argv[0] to a secret-looking, slash-free
    string (``exec -a NAME`` -- the same trick a compromised/malicious child
    or a library like setproctitle could use) must not have that string
    echoed back as the stored "name". ``Path(argv[0]).name`` is a no-op on a
    slash-free string, so storing name from argv[0] directly would leak it
    verbatim; the stored name must instead come from the kernel-reported
    process name (comm), which reflects the exec()'d binary ("sleep"), not
    the rewritten argv[0]."""
    p = subprocess.Popen(
        ["bash", "-c", "exec -a PGPASSWORD=hunter2-supersecret sleep 30 &\nwait\n"],
        start_new_session=True,
    )
    try:
        live, ignored = _wait_for_children(p, lambda live, _ignored: bool(live))
        assert live, "the backgrounded child never appeared -- nothing was actually checked"
        blob = repr(live + ignored)
        assert "hunter2-supersecret" not in blob and "PGPASSWORD" not in blob
        assert all(c["name"] == "sleep" for c in live)
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_matched_records_pattern_not_argv():
    """Ignore-pattern matches on full cmdline; the stored ignored entry records
    the matched pattern string + basename, never the full argv."""
    # Background a subshell that exec-replaces itself with the secret in argv[0].
    # _live_children sees the child process; matching fires on its full cmdline.
    p = subprocess.Popen(
        ["bash", "-c", "bash -c 'exec -a sk-MATCHME sleep 30' &\nwait"],
        start_new_session=True,
    )
    try:
        # Wait for the INNER bash to actually exec into sk-MATCHME (not just
        # fork) -- polling on `ignored` non-empty, rather than a fixed sleep,
        # so a slow exec under load never races a too-early check.
        live, ignored = _wait_for_children(
            p, lambda _live, ignored: bool(ignored), ignore_patterns=[re.compile(r"sk-MATCHME")]
        )
        assert ignored and ignored[0]["matched"] == "sk-MATCHME"
        # Only name/pid/matched stored — not the raw cmdline
        assert all(set(c) == {"name", "pid", "matched"} for c in ignored)
        assert ignored[0]["name"] != "sk-MATCHME"  # name is comm-derived, not argv[0]
    finally:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        p.wait()


def test_kill_pgroup_reentrant_sigterm_during_grace_still_sigkills(tmp_path):
    """round_cmd's SIGTERM handler stays installed for the whole process life
    (every SIGTERM raises a fresh KeyboardInterrupt, not just the first), so a
    second, impatient SIGTERM landing while _kill_pgroup waits out its grace
    period re-raises INSIDE the grace-sleep loop. Unshielded, that interrupt
    would propagate out of _kill_pgroup before SIGKILL ever ran, leaving a
    TERM-ignoring agent alive forever. FakeClock makes this deterministic: the
    first clock.sleep() call raises (simulating the re-entrant signal); the
    function must swallow it and keep going until it SIGKILLs the child."""
    from agent_runner.agent_runtime import _kill_pgroup
    from tests._clock import FakeClock

    ready = tmp_path / "trap.ready"
    # A ready marker (not a fixed sleep) makes "the trap is installed before we
    # TERM it" deterministic under load -- a fixed sleep raced bash startup
    # under a busy full-suite run and could see the child die on the FIRST
    # (un-trapped) SIGTERM, exiting the grace loop after only one flaky_sleep
    # call instead of exercising the shield across the full grace window.
    script = _write_fake_script(tmp_path, f'trap "" TERM\ntouch "{ready}"\nsleep 30\n')
    proc = subprocess.Popen([str(script)], start_new_session=True)
    try:
        # 40s (widened from 15s, then from the original 5s): measured under
        # `-n auto` contention on a busy host, bash's own fork+exec
        # occasionally starved for several real seconds before it got
        # scheduled to run the trap+touch line; reproduced failing at 15s
        # under >=2 concurrent gates (~2-3x CPU oversubscription).
        assert wait_for(tmp_path, ready.exists, timeout_s=40), (
            "child never installed its SIGTERM trap"
        )
        clock = FakeClock()
        real_sleep = clock.sleep
        calls = {"n": 0}

        def flaky_sleep(seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt("re-entrant SIGTERM during grace")
            real_sleep(seconds)

        clock.sleep = flaky_sleep

        _kill_pgroup(proc, clock)

        assert calls["n"] >= 2, "the shield must retry after the re-entrant interrupt"
        assert proc.wait(timeout=5) is not None  # SIGKILL reaped it despite the interrupt
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_hard_wall_fires_on_monotonic_despite_epoch_warp(tmp_path):
    """R1128 hard-wall is measured on monotonic time: a mid-round NTP step (an
    epoch jump, routine on the RTC-less Pi fleet) must not disable or distort it.
    Pre-fix the timeout compared epoch deltas — a backward warp made now-start<0
    so the wall never fired."""
    from tests._clock import FakeClock

    script = _write_fake_script(tmp_path, "sleep 100\n")  # never produces a result
    clock = FakeClock(epoch=1000.0)
    warped: list[bool] = []

    def _on_progress(_stats):
        if not warped:
            clock.warp_epoch(-3600)  # NTP steps the wall clock back an hour mid-round
            warped.append(True)

    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,
        log_path=tmp_path / "round.log",
        env_extra={},
        progress_callback=_on_progress,
        progress_interval_s=1,
        clock=clock,
    )
    assert result.timed_out is True  # hard-wall still fired (monotonic, NTP-immune)
    assert warped  # the epoch warp really happened mid-round
    assert 0 < result.duration_s < 60  # monotonic duration, not the -3600 epoch delta
