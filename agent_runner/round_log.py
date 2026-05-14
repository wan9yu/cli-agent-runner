"""Round-log file operations for serve_cmd.

Extracted from serve_cmd to keep that module a thin dispatcher.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_runner.api import read_round_num

ROUND_CURRENT_LINK = "round-current.log"


def atomic_relink(link: Path, target: Path) -> None:
    """Atomically replace ``link`` to point at ``target``.

    Uses ``os.symlink`` + ``os.replace``: create the symlink at a temp path,
    then atomically rename it to the final link name.
    """
    tmp = link.with_suffix(link.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    os.symlink(target.name, tmp)
    os.replace(tmp, link)


def prune_old_round_logs(log_dir: Path, retention: int) -> None:
    """Keep most-recent ``retention`` round-*.log files by mtime; unlink the rest.

    Excludes the ``round-current.log`` symlink — that's not a regular log file.
    Called once at serve startup (no mid-session pruning, avoid race with
    active writes).
    """
    logs = sorted(
        log_dir.glob("round-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    logs = [p for p in logs if p.name != ROUND_CURRENT_LINK]
    for old in logs[retention:]:
        old.unlink(missing_ok=True)


def next_round_num(log_dir: Path) -> int:
    """Return the next round number, avoiding reuse of any existing log file numbers.

    Takes ``max(read_round_num, max_log_file_num) + 1``. Under normal operation
    these agree. The file-system fallback handles the case where ``status.json``
    has been deleted but old ``round-*.log`` files remain — the counter skips
    forward instead of silently overwriting a numbered log.
    """
    status_num = read_round_num(log_dir)
    file_nums = []
    for p in log_dir.glob("round-*.log"):
        if p.name == ROUND_CURRENT_LINK:
            continue
        stem_parts = p.stem.split("-", 1)
        if len(stem_parts) == 2:
            try:
                file_nums.append(int(stem_parts[1]))
            except ValueError:
                pass
    max_file_num = max(file_nums, default=0)
    return max(status_num, max_file_num) + 1
