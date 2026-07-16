from agent_runner.agent_runtime import RunResult
from agent_runner.runner import _exit_cause  # small pure helper we add


def _rr(exit_code, timed_out=False):
    return RunResult(exit_code=exit_code, duration_s=1.0, timed_out=timed_out, pid=1)


def test_exit_cause_mapping():
    assert _exit_cause(_rr(0)) == "clean"
    assert _exit_cause(_rr(1)) == "error"
    assert _exit_cause(_rr(143)) == "signal:SIGTERM"
    assert _exit_cause(_rr(-15)) == "signal:SIGTERM"
    # timeout wins even though agent-runner signal-killed it to enforce the timeout
    assert _exit_cause(_rr(-15, timed_out=True)) == "timeout"
