"""Agent subprocess management — spawns the configured agent CLI process.

Defenses encoded here:
- R725: SIGTERM handler reaps process group before runner exits
- R1128: ROUND_TIMEOUT is wall-clock hard wall (no activity-based extension)
- #307: start_new_session=True isolates subprocess in its own pgrp
- env injection: per-CLI envs come from AgentConfig.env (preset-supplied);
  no implicit injection in this module.
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
    killed_for_grace: bool = False


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


# Exact compact bytes — matches claude CLI's no-whitespace JSONL output.
# A future CLI variant emitting `{"type": "result", ...}` (with space) would
# bypass this scan; revisit if that happens.
_RESULT_MARKER = b'"type":"result"'


def run(
    *,
    command: list[str],
    prompt_arg_template: list[str],
    prompt: str,
    timeout_s: int,
    log_path: Path,
    env_extra: dict[str, str],
    max_grace_after_result_s: int = 0,
    progress_callback: Callable[[dict], None] | None = None,
    progress_interval_s: int = 0,
) -> RunResult:
    """Spawn the agent subprocess and wait for exit or timeout.

    Wall-clock timeout (R1128). On timeout: SIGTERM pgroup → REAP_GRACE_S → SIGKILL.

    max_grace_after_result_s: when > 0, start a countdown after the first
    type=result event is detected in the log; kill if subprocess is still
    running after this many seconds (HUNG defense). 0 = disabled.

    progress_callback: when not None and progress_interval_s > 0, called every
    progress_interval_s seconds with a dict of log stats (log_size_kb,
    last_write_age_s, wall_age_s). Keeps agent_runtime event-free; callers
    build the callback to emit events.
    """
    argv = _build_argv(command, prompt_arg_template, prompt)
    env = {**os.environ, **env_extra}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    start = time.time()
    last_progress_at = start
    proc = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    result_seen_at: float | None = None
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
            # Grace kill: result emitted but subprocess still running
            if max_grace_after_result_s > 0:
                if result_seen_at is None:
                    # Cheap check: byte-scan log for marker substring
                    try:
                        with log_path.open("rb") as f:
                            if _RESULT_MARKER in f.read():
                                result_seen_at = now
                    except OSError:
                        pass  # log not flushed yet; check next tick
                if result_seen_at is not None and now - result_seen_at > max_grace_after_result_s:
                    _kill_pgroup(proc)
                    duration = time.time() - start
                    exit_code = proc.returncode if proc.returncode is not None else -1
                    return RunResult(
                        exit_code=exit_code,
                        duration_s=duration,
                        timed_out=True,
                        pid=proc.pid,
                        killed_for_grace=True,
                    )
            # Progress heartbeat: call back if interval elapsed
            if progress_callback is not None and progress_interval_s > 0:
                if now - last_progress_at >= progress_interval_s:
                    try:
                        st = log_path.stat()
                        log_size_kb = st.st_size // 1024
                        last_write_age_s = max(0, int(now - st.st_mtime))
                    except OSError:
                        log_size_kb = 0
                        last_write_age_s = 0
                    progress_callback(
                        {
                            "log_size_kb": log_size_kb,
                            "last_write_age_s": last_write_age_s,
                            "wall_age_s": int(now - start),
                        }
                    )
                    last_progress_at = now
            time.sleep(0.2)
    finally:
        log_file.close()


def install_sigterm_reaper(reaper: Callable[[], None]) -> object:
    """Install a SIGTERM handler that calls ``reaper()`` first.

    R725 defense: when the supervisor receives SIGTERM (e.g. systemctl stop,
    manual kill), the bash wrapper would otherwise respawn a fresh runner
    while the old agent keeps running → two agent processes race on the same
    git tree, the second commit can swallow the first commit's chat-room
    entry. Reaper terminates pgroup first.

    Returns the previous SIGTERM handler so caller can restore it.
    """

    def _handler(_signum: int, _frame: object) -> None:
        reaper()

    return signal.signal(signal.SIGTERM, _handler)
