"""Round-log file operations for serve_cmd.

Extracted from serve_cmd to keep that module a thin dispatcher.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from agent_runner.api import read_round_num

ROUND_CURRENT_LINK = "round-current.log"
_AGENT_ROUND_LOG_RE = re.compile(r"^R(\d+)-")


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


def prune_rounds_dir(rounds_dir: Path, keep: int) -> int:
    """Keep the ``keep`` highest-numbered ``R<n>-*.log`` agent logs; unlink the rest.

    Ordered by the round number parsed out of the filename — not by string sort
    (``R9`` must not outrank ``R10``) and not by mtime (a restore or copy can
    rewrite that; the round number is the semantic order). Names that don't
    match are left alone. Called at round start, before the current round's log
    is minted, so the active log is never a deletion candidate.

    Returns the number of files unlinked.
    """
    if not rounds_dir.is_dir():
        return 0
    numbered = []
    for path in rounds_dir.glob("R*.log"):
        match = _AGENT_ROUND_LOG_RE.match(path.name)
        if match:
            numbered.append((int(match.group(1)), path))
    # Sort on the number alone: the same round can be present twice with
    # different timestamps after a crash-rerun, and tuple comparison would then
    # fall through to comparing Paths.
    numbered.sort(key=lambda item: item[0], reverse=True)
    stale = numbered[keep:]
    for _num, old in stale:
        old.unlink(missing_ok=True)
    return len(stale)


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
