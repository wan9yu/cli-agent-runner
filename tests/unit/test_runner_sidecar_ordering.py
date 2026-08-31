"""B6: the holder sidecar is unlinked BEFORE the flock fd is closed, so it can never
be deleted after the lock is released (which would race away a new holder's sidecar)."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

from agent_runner import runner
from agent_runner.cli.common import cfg_from_args
from tests._test_helpers import make_toml


def test_sidecar_unlinked_before_close(monkeypatch, tmp_path):
    cfg_path = make_toml(tmp_path)  # agent command = ["true"]
    # make_toml does NOT git-init; without a repo run_one_round's startup battery
    # sys.exit(78)s before the sidecar code ever runs.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    cfg = cfg_from_args(SimpleNamespace(config=cfg_path))
    log_dir = cfg.runtime.log_dir
    sidecar = log_dir / "agent-runner.lock.holder"

    observed = {}
    real_close = os.close

    def spy_close(fd):
        observed["sidecar_existed_at_close"] = sidecar.exists()
        return real_close(fd)

    monkeypatch.setattr(runner.os, "close", spy_close)
    runner.run_one_round(cfg)

    assert observed["sidecar_existed_at_close"] is False
