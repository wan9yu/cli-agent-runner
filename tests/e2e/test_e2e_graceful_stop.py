from __future__ import annotations

import json
import time

from .conftest import _ssh

_POLL_INTERVAL_S = 0.5


def _wait_until(predicate, timeout_s: float) -> bool:
    """Poll ``predicate`` (an ssh round-trip) until it's true or the deadline passes.

    Real ssh latency to the pi is unpredictable, so a fixed sleep is either too
    short (flaky) or wastefully long; polling for the actual on-disk evidence
    (pid file present/absent) is both faster on the common path and correct
    under load."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return predicate()


def test_serve_should_exit_after_current_round_when_stopped(
    pi_install_agent_runner: str,
    pi_workdir: str,
    pi_config: str,
) -> None:
    bg = (
        f"nohup bash -c 'FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} serve' > /dev/null 2>&1 & echo $!"
    )
    pid = _ssh(bg).stdout.strip()
    pid_file = f"{pi_workdir}/logs/serve.pid"
    _wait_until(lambda: _ssh(f"test -f {pid_file}", check=False).returncode == 0, timeout_s=10)

    stop_cmd = f"{pi_install_agent_runner} --config {pi_config} stop"
    _ssh(stop_cmd, check=False)
    _wait_until(lambda: _ssh(f"test -f {pid_file}", check=False).returncode != 0, timeout_s=15)

    r = _ssh(f"cat {pid_file} 2>/dev/null", check=False)
    assert r.stdout.strip() == "" or r.returncode != 0
    status_raw = _ssh(f"cat {pi_workdir}/logs/status.json").stdout
    status = json.loads(status_raw)
    assert status["round_num"] >= 1
    _ssh(f"kill {pid} 2>/dev/null || true", check=False)
