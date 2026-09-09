from agent_runner.agent_runtime import RunResult
from agent_runner.runner import _exit_cause  # small pure helper we add


def _rr(exit_code, timed_out=False, killed_for_grace=False):
    return RunResult(
        exit_code=exit_code,
        duration_s=1.0,
        timed_out=timed_out,
        pid=1,
        killed_for_grace=killed_for_grace,
    )


def test_exit_cause_should_return_clean_when_returncode_zero():
    assert _exit_cause(_rr(0)) == "clean"


def test_exit_cause_should_return_error_when_returncode_nonzero():
    assert _exit_cause(_rr(1)) == "error"


def test_exit_cause_should_return_signal_cause_when_process_was_signal_killed():
    assert _exit_cause(_rr(143)) == "signal:SIGTERM"
    assert _exit_cause(_rr(-15)) == "signal:SIGTERM"


def test_exit_cause_should_return_timeout_when_timed_out_and_not_grace_killed():
    # timeout wins even though agent-runner signal-killed it to enforce the timeout
    assert _exit_cause(_rr(-15, timed_out=True)) == "timeout"


def test_exit_cause_should_return_grace_kill_when_killed_for_grace_even_though_timed_out():
    # a grace-kill also sets timed_out, but it is NOT a hung round → distinct cause
    assert _exit_cause(_rr(-15, timed_out=True, killed_for_grace=True)) == "grace_kill"
