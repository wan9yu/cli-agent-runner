"""Property tests (v0.3.5 wall-clock pattern) for the typed cooperative_stop
signal — the safety-critical half of v0.3.9 §B.

Two REAL child processes, one shared dual-trap script, exercise the two
``agent_runtime.run`` termination call sites that funnel through the single
``_kill_pgroup`` primitive:

- the cooperative-stop site (``run``'s ``except BaseException`` reap, which
  serve's SIGTERM-to-the-leader drives via round_cmd's SIGTERM->KeyboardInterrupt
  handler) MUST deliver the AGENT's declared signal (SIGINT here); and
- the R1128 hard-wall site MUST deliver SIGTERM first REGARDLESS of that
  declared cooperative signal.

The child records which signal actually arrived (a marker file named after the
signal), so these assert the PROPERTY the agent experiences, not the argument a
mock was called with — the v0.3.3 mechanism-vs-property guard, made executable.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from agent_runner import agent_runtime

# Traps BOTH SIGINT and SIGTERM, records the FIRST one received as a marker file
# named after it, then exits cleanly (so the reap sees a within-grace exit).
# Writes ``ready`` only AFTER both handlers are installed: a freshly Popen'd
# interpreter needs a moment before the handlers take effect, and a signal
# landing in that gap kills the child by the OS default action instead of the
# trap -- the same real race the v0.3.5 grace property test documents.
_DUAL_TRAP_CHILD = (
    "import os, signal, sys, time\n"
    "d = sys.argv[1]\n"
    "def _h(signum, _frame):\n"
    "    open(os.path.join(d, signal.Signals(signum).name), 'w').write('x')\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGINT, _h)\n"
    "signal.signal(signal.SIGTERM, _h)\n"
    "open(os.path.join(d, 'ready'), 'w').write('x')\n"
    "time.sleep(30)\n"
)


def _write_child(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "dual_trap_agent.py"
    script.write_text(_DUAL_TRAP_CHILD, encoding="utf-8")
    markers = tmp_path / "markers"
    markers.mkdir()
    return script, markers


@pytest.mark.serial
@pytest.mark.timeout(30)
def test_agent_should_receive_declared_cooperative_signal_when_stopped(tmp_path):
    """PROPERTY A: on a cooperative stop, the agent RECEIVES the signal its
    preset declares (SIGINT), not SIGTERM. Drives ``run``'s ``except
    BaseException`` reap -- the exact call site serve's leader-SIGTERM reaches --
    by raising out of the progress callback once the child is confirmed up."""
    if not hasattr(os, "killpg"):
        pytest.skip("no killpg on this platform -- POSIX-only property")

    script, markers = _write_child(tmp_path)
    log_path = tmp_path / "round.log"

    def _cooperative_stop(_stats: dict) -> None:
        # Fire the stop only after the child installed its traps, so SIGINT
        # cannot race the handler install and kill by default disposition.
        if (markers / "ready").exists():
            raise RuntimeError("supervisor cooperative stop")

    with pytest.raises(RuntimeError):
        agent_runtime.run(
            command=[sys.executable, str(script), str(markers)],
            prompt_arg_template=[],
            prompt="x",
            timeout_s=30,
            work_dir=tmp_path,
            log_path=log_path,
            env_extra={},
            progress_callback=_cooperative_stop,
            progress_interval_s=1,
            cooperative_first_signal=signal.SIGINT,
        )

    assert (markers / "SIGINT").exists(), (
        "agent did not receive its declared SIGINT on the cooperative-stop path"
    )
    assert not (markers / "SIGTERM").exists(), (
        "cooperative-stop path leaked a SIGTERM instead of the declared SIGINT"
    )


@pytest.mark.serial
@pytest.mark.timeout(30)
def test_hard_wall_should_send_sigterm_first_regardless_of_cooperative_stop_when_invoked(tmp_path):
    """PROPERTY B (the mechanism-vs-property guard): a round hitting the R1128
    wall gets SIGTERM FIRST even though ``cooperative_first_signal`` is SIGINT --
    the hard wall must send SIGTERM literally, never the cooperative signal."""
    if not hasattr(os, "killpg"):
        pytest.skip("no killpg on this platform -- POSIX-only property")

    script, markers = _write_child(tmp_path)
    log_path = tmp_path / "round.log"

    result = agent_runtime.run(
        command=[sys.executable, str(script), str(markers)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,  # R1128 wall fires ~2s in, well after the ~0.3s trap install
        work_dir=tmp_path,
        log_path=log_path,
        env_extra={},
        # A cooperative signal is configured, yet the hard wall must ignore it:
        cooperative_first_signal=signal.SIGINT,
    )

    assert result.timed_out, "expected the round to hit the R1128 wall"
    assert not (markers / "SIGINT").exists(), (
        "R1128 hard wall leaked the cooperative SIGINT -- it must send SIGTERM first"
    )
    assert (markers / "SIGTERM").exists(), "R1128 hard wall did not send SIGTERM to the agent first"
