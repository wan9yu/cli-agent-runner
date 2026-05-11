from __future__ import annotations

import json
import time

from .conftest import _ssh


def test_given_serve_running_when_stop_then_exits_after_current_round(
    pi_install_agent_runner: str,
    pi_workdir: str,
    pi_config: str,
) -> None:
    bg = (
        f"nohup bash -c 'FAKE_AGENT_BEHAVIOR=succeed WORK_DIR={pi_workdir} "
        f"{pi_install_agent_runner} --config {pi_config} serve' > /dev/null 2>&1 & echo $!"
    )
    pid = _ssh(bg).stdout.strip()
    time.sleep(2)
    stop_cmd = f"{pi_install_agent_runner} --config {pi_config} stop"
    _ssh(stop_cmd, check=False)
    time.sleep(8)
    r = _ssh(f"cat {pi_workdir}/logs/serve.pid 2>/dev/null", check=False)
    assert r.stdout.strip() == "" or r.returncode != 0
    status_raw = _ssh(f"cat {pi_workdir}/logs/status.json").stdout
    status = json.loads(status_raw)
    assert status["round_num"] >= 1
    _ssh(f"kill {pid} 2>/dev/null || true", check=False)
