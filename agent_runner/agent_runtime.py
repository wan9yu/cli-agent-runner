"""Agent subprocess management — ONLY module that spawns the claude CLI.

Defenses encoded here:
- R725: SIGTERM handler reaps process group before runner exits
- R1128: ROUND_TIMEOUT is wall-clock hard wall (no activity-based extension)
- #307: start_new_session=True isolates subprocess in its own pgrp
- env injection: DISABLE_AUTOUPDATER=1 + CLAUDE_CODE_EFFORT_LEVEL caller-provided
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251 — sanctioned subprocess caller
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REAP_GRACE_S = 5


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    duration_s: float
    timed_out: bool
    pid: int


def _build_argv(command: list[str], prompt_arg_template: list[str], prompt: str) -> list[str]:
    """Build full argv: command + prompt args (with {prompt} substituted)."""
    return list(command) + [a.replace("{prompt}", prompt) for a in prompt_arg_template]


def _kill_pgroup(proc: subprocess.Popen) -> None:
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + REAP_GRACE_S
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run(
    *,
    command: list[str],
    prompt_arg_template: list[str],
    prompt: str,
    timeout_s: int,
    log_path: Path,
    env_extra: dict[str, str],
) -> RunResult:
    """Spawn the agent subprocess and wait for exit or timeout.

    Wall-clock timeout (R1128). On timeout: SIGTERM pgroup → REAP_GRACE_S → SIGKILL.
    """
    argv = _build_argv(command, prompt_arg_template, prompt)
    env = {**os.environ, **env_extra}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    start = time.time()
    proc = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        while True:
            ret = proc.poll()
            now = time.time()
            if ret is not None:
                duration = now - start
                return RunResult(exit_code=ret, duration_s=duration, timed_out=False, pid=proc.pid)
            if now - start > timeout_s:
                _kill_pgroup(proc)
                duration = time.time() - start
                exit_code = proc.returncode if proc.returncode is not None else -1
                return RunResult(
                    exit_code=exit_code, duration_s=duration, timed_out=True, pid=proc.pid
                )
            time.sleep(0.2)
    finally:
        log_file.close()


CRITICAL_ENV_DEFAULTS: dict[str, str] = {
    "DISABLE_AUTOUPDATER": "1",  # do not let claude self-update mid-loop
    "CLAUDE_CODE_EFFORT_LEVEL": "xhigh",  # full effort, not default
}


def merge_critical_envs(user_env: dict[str, str]) -> dict[str, str]:
    """Merge user env with CRITICAL_ENV_DEFAULTS — critical always wins."""
    merged = dict(user_env)
    merged.update(CRITICAL_ENV_DEFAULTS)
    return merged


def install_sigterm_reaper(reaper: Callable[[], None]) -> object:
    """Install a SIGTERM handler that calls ``reaper()`` first.

    R725 defense: when supervisor receives SIGTERM (e.g. systemctl stop, manual
    kill), bash wrapper would otherwise respawn fresh runner while old claude
    keeps running → two claudes race on the same git tree, second commit can
    swallow first commit's chat-room entry. Reaper terminates pgroup first.

    Returns the previous SIGTERM handler so caller can restore it.
    """

    def _handler(_signum: int, _frame: object) -> None:
        reaper()

    return signal.signal(signal.SIGTERM, _handler)
