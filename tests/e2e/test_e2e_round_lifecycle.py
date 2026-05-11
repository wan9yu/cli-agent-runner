"""End-to-end on pi (Pi Zero 2 W, ARM Linux, 463MB RAM).

Skipped unless ``AGENT_RUNNER_E2E_PI=1`` is set. Tests use the `pi` ssh alias.
"""

from __future__ import annotations

import json
import time

from .conftest import _ssh


def test_given_fake_agent_succeeds_on_pi_when_round_runs_then_status_marks_completed(
    pi_install_agent_runner: str, pi_config: str, pi_workdir: str,
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


def test_given_three_supervisor_invocations_on_pi_when_runs_then_round_num_monotonic(
    pi_install_agent_runner: str, pi_config: str, pi_workdir: str,
) -> None:
    base = (
        f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )
    for expected in (1, 2, 3):
        _ssh(base)
        status = json.loads(_ssh(f"cat {pi_workdir}/logs/status.json").stdout)
        assert status["round_num"] == expected


def test_given_fake_agent_dirty_on_pi_when_round_runs_then_orphan_stashed(
    pi_install_agent_runner: str, pi_config: str, pi_workdir: str,
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


def test_given_fake_agent_hangs_on_pi_when_timeout_exceeds_then_killed(
    pi_install_agent_runner: str, pi_config: str, pi_workdir: str,
) -> None:
    cmd = (
        f"FAKE_AGENT_BEHAVIOR=hang WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round"
    )
    start = time.time()
    _ssh(cmd, check=False)
    elapsed = time.time() - start
    assert elapsed < 30  # timeout=10 + reap=5 + ssh overhead


def test_given_concurrent_supervisor_on_pi_when_second_starts_then_exits_with_lock(
    pi_install_agent_runner: str, pi_config: str, pi_workdir: str,
) -> None:
    """Spawn one in background (long-hanging via FAKE_AGENT_BEHAVIOR=hang),
    then try to start a second; second must fail with non-zero exit."""
    bg = (
        f"nohup bash -c 'FAKE_AGENT_BEHAVIOR=hang WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} round' "
        "> /dev/null 2>&1 & echo $!"
    )
    pid = _ssh(bg).stdout.strip()
    time.sleep(2)  # let first acquire lock
    try:
        cmd = (
            f"FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
            f"{pi_install_agent_runner} --config {pi_config} round"
        )
        r = _ssh(cmd, check=False)
        assert r.returncode != 0
    finally:
        _ssh(f"kill {pid} 2>/dev/null || true", check=False)
