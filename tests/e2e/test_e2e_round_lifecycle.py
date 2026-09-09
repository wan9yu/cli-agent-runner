"""End-to-end on pi (Pi Zero 2 W, ARM Linux, 463MB RAM).

Skipped unless ``AGENT_RUNNER_E2E_PI=1`` is set. Tests use the `pi` ssh alias.
"""

from __future__ import annotations

import json
import time

from .conftest import _ssh

_POLL_INTERVAL_S = 0.5


def _wait_until(predicate, timeout_s: float) -> bool:
    """Poll ``predicate`` (an ssh round-trip) until it's true or the deadline passes.

    Real ssh latency to the pi is unpredictable, so a fixed sleep is either too
    short (flaky) or wastefully long; polling for the actual on-disk evidence
    (the lock holder sidecar) is both faster on the common path and correct
    under load."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return predicate()


def test_round_should_mark_status_completed_when_fake_agent_succeeds_on_pi(
    pi_install_agent_runner: str,
    pi_config: str,
    pi_workdir: str,
) -> None:
    cmd = (
        f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )

    r = _ssh(cmd)

    assert r.returncode == 0
    status_raw = _ssh(f"cat {pi_workdir}/logs/status.json").stdout
    status = json.loads(status_raw)
    assert status["round_num"] == 1
    assert status["last_exit_code"] == 0


def test_round_num_should_increase_monotonically_when_supervisor_invoked_repeatedly_on_pi(
    pi_install_agent_runner: str,
    pi_config: str,
    pi_workdir: str,
) -> None:
    base = (
        f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )

    for expected in (1, 2, 3):
        _ssh(base)
        status = json.loads(_ssh(f"cat {pi_workdir}/logs/status.json").stdout)
        assert status["round_num"] == expected


def test_round_should_stash_orphan_changes_when_fake_agent_leaves_dirty_worktree_on_pi(
    pi_install_agent_runner: str,
    pi_config: str,
    pi_workdir: str,
) -> None:
    cmd_dirty = (
        f"FAKE_AGENT_BEHAVIOR=dirty WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )
    _ssh(cmd_dirty)
    cmd_succeed = (
        f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )
    _ssh(cmd_succeed)

    ctx = json.loads(_ssh(f"cat {pi_workdir}/logs/round-context.json").stdout)
    assert "orphan_stash" in ctx
    assert ctx["orphan_stash"]["ref"]


def test_round_should_be_killed_when_fake_agent_hangs_past_timeout_on_pi(
    pi_install_agent_runner: str,
    pi_config: str,
    pi_workdir: str,
) -> None:
    cmd = (
        f"FAKE_AGENT_BEHAVIOR=hang WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )

    start = time.time()
    _ssh(cmd, check=False)
    elapsed = time.time() - start

    # timeout=10 + reap=5 + generous ssh/interpreter-startup headroom for a
    # possibly-loaded Pi Zero 2 W (widened from 30 -- see module docstring
    # above for the hardware this suite targets)
    assert elapsed < 60


def test_second_supervisor_should_exit_nonzero_when_lock_held_by_first_on_pi(
    pi_install_agent_runner: str,
    pi_config: str,
    pi_workdir: str,
) -> None:
    """Spawn one in background (long-hanging via FAKE_AGENT_BEHAVIOR=hang),
    then try to start a second; second must fail with non-zero exit."""
    bg = (
        f"nohup bash -c 'FAKE_AGENT_BEHAVIOR=hang WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round' "
        "> /dev/null 2>&1 & echo $!"
    )
    pid = _ssh(bg).stdout.strip()
    lock_holder = f"{pi_workdir}/logs/agent-runner.lock.holder"
    _wait_until(lambda: _ssh(f"test -f {lock_holder}", check=False).returncode == 0, timeout_s=10)

    try:
        cmd = (
            f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
            f"{pi_install_agent_runner} --config {pi_config} round"
        )
        r = _ssh(cmd, check=False)

        assert r.returncode != 0
    finally:
        _ssh(f"kill {pid} 2>/dev/null || true", check=False)
