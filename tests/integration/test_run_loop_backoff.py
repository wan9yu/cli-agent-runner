from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_LOOP = REPO_ROOT / "deploy" / "run-loop.sh"


@pytest.mark.skipif(not RUN_LOOP.exists(), reason="run-loop.sh not yet created")
def test_given_failing_command_when_run_loop_runs_then_backs_off_exponentially(
    tmp_path: Path,
) -> None:
    """Mock agent-runner with a failing script; assert backoff sequence in stderr."""
    fake_cmd = tmp_path / "agent-runner"
    fake_cmd.write_text(
        f"#!/usr/bin/env bash\necho 'invocation' >> {tmp_path}/invocations\nexit 1\n"
    )
    fake_cmd.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["RESTART_DELAY"] = "1"  # base delay 1s for fast test
    env["MAX_DELAY"] = "8"

    proc = subprocess.Popen(
        ["bash", str(RUN_LOOP)],
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    time.sleep(8)  # let backoff progress: 1, 2, 4 → ~7s wait + invocations
    proc.terminate()
    try:
        _, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()

    delays = [int(m) for m in re.findall(r"backoff (\d+)s", stderr)]
    assert delays[:3] == [1, 2, 4], f"expected exponential 1,2,4 got {delays}"
