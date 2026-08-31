"""Round-log file operations for serve_cmd.

Extracted from serve_cmd to keep that module a thin dispatcher.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from agent_runner.api import read_round_num

ROUND_CURRENT_LINK = "round-current.log"
_AGENT_ROUND_LOG_RE = re.compile(r"^R(\d+)-")


def open_round_log(path: Path) -> TextIO:
    """Text-mode opener for round logs, pinning ``errors="replace"``.

    Round logs are the merged, untrusted stdout+stderr of an agent subprocess and
    routinely contain non-UTF-8 bytes. Every text-mode round-log read goes through
    here so one place owns the decode policy; the ``"rb"`` marker scan in
    agent_runtime is the only exempt reader (it needs raw bytes).
    """
    return path.open("r", encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class PruneOutcome:
    """What one prune pass did to a round-log family.

    ``deleted`` and ``deferred`` are never both nonzero: a pass either prunes
    normally, defers wholesale (see the bulk guard below), or does not run at
    all (pruning disabled). ``existing`` is the total number of files the pass
    considered — carried so a caller reporting a deferral does not have to
    re-derive it from the slice rule.
    """

    deleted: int
    deferred: int
    existing: int


def _pruning_disabled(keep: int) -> bool:
    """``keep == 0`` disables pruning entirely — the default.

    Distinct from a deferral, and deliberately reported as neither deleted nor
    deferred: 0 is the operator's expressed intent, not a backlog awaiting a
    decision, so callers emit nothing. Unbounded growth is the accepted trade
    and it is not undefended — ``disk_warning`` (90%) alerts and
    ``disk_critical`` (95%) auto-stops the service.
    """
    return keep == 0


def _is_bulk(stale: int, keep: int) -> bool:
    """A prune that would remove more files than it keeps is a *bulk* prune.

    Bulk prunes delete nothing. In steady state a pass removes ~1 file, so
    this can only trip on the first encounter with a pre-existing backlog or
    after an operator drastically lowers ``runtime.round_log_retention`` —
    both cases where wiping history is far likelier to be an accident than an
    intent. The supervisor never performs a bulk deletion on its own; the
    operator raises retention (the backlog then falls inside the kept window
    and normal pruning resumes) or clears the files themselves.
    """
    return stale > keep


def _mtime_or_none(path: Path) -> float | None:
    """``path``'s mtime, or ``None`` if it no longer exists or is a symlink.

    Bridges the TOCTOU gap between ``glob()`` enumerating a file and the
    ``lstat()`` call here: a concurrent cleanup / logrotate / another tool
    churning the log dir at serve startup can delete an entry in between. That
    race is routine, not an error — the file being gone already means
    "nothing to prune here" — so it degrades to "skip this entry", never
    propagates ``FileNotFoundError`` (which would otherwise reach
    ``serve_cmd._prepare_loop``'s deterministic-startup guard and misclassify
    a transient race as a permanent config failure).

    Uses ``lstat`` (not ``stat``) so a ``round-*.log`` entry that turns out to
    be a symlink is inspected without following it — a dangling target would
    otherwise raise ``FileNotFoundError`` here, and a live target would
    wrongly borrow the target's mtime and get treated as a real log file. A
    symlink (dangling or not) is never a real round log, so it degrades the
    same way as "vanished": excluded from consideration entirely, never a
    deletion candidate.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        return None
    return st.st_mtime


def atomic_relink(link: Path, target: Path) -> None:
    """Atomically replace ``link`` to point at ``target``.

    Uses ``os.symlink`` + ``os.replace``: create the symlink at a temp path,
    then atomically rename it to the final link name.
    """
    tmp = link.with_suffix(link.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    os.symlink(target.name, tmp)
    os.replace(tmp, link)


def prune_old_round_logs(log_dir: Path, retention: int) -> PruneOutcome:
    """Keep most-recent ``retention`` round-*.log files by mtime; unlink the rest.

    Excludes the ``round-current.log`` symlink — that's not a regular log file.
    Called once at serve startup (no mid-session pruning, avoid race with
    active writes).

    ``retention == 0`` (the default) never prunes. Otherwise subject to the
    bulk guard (:func:`_is_bulk`): a startup that would delete more than it
    keeps deletes nothing and reports the deferral instead. ``round_log_retention``
    governs both round-log families, so both opting in and lowering the value
    mean the same thing in both.
    """
    if _pruning_disabled(retention):
        return PruneOutcome(deleted=0, deferred=0, existing=0)
    present = []
    for p in log_dir.glob("round-*.log"):
        # Exclude round-current.log up front — a dangling symlink here must
        # never even reach lstat, let alone the sort. Any *other* round-*.log
        # symlink (unexpected, but defended anyway) is excluded by
        # _mtime_or_none returning None for it.
        if p.name == ROUND_CURRENT_LINK:
            continue
        mtime = _mtime_or_none(p)
        if mtime is not None:
            present.append((p, mtime))
    logs = [p for p, _mtime in sorted(present, key=lambda pair: pair[1], reverse=True)]
    stale = logs[retention:]
    if _is_bulk(len(stale), retention):
        return PruneOutcome(deleted=0, deferred=len(stale), existing=len(logs))
    for old in stale:
        old.unlink(missing_ok=True)
    return PruneOutcome(deleted=len(stale), deferred=0, existing=len(logs))


def prune_rounds_dir(rounds_dir: Path, keep: int) -> PruneOutcome:
    """Keep the ``keep`` highest-numbered ``R<n>-*.log`` agent logs; unlink the rest.

    Ordered by the round number parsed out of the filename — not by string sort
    (``R9`` must not outrank ``R10``) and not by mtime (a restore or copy can
    rewrite that; the round number is the semantic order). Names that don't
    match are left alone. Called at round start, before the current round's log
    is minted, so the active log is never a deletion candidate.

    ``keep == 0`` (the default) never prunes: these transcripts are forensic
    material — the record used to reconstruct a round lost to a dropped
    connection, or to verify that a fix actually worked — so retaining them is
    the default and discarding them is opt-in. When pruning IS enabled, the
    bulk guard (:func:`_is_bulk`) still applies: a pass that would delete more
    than it keeps deletes nothing, so opting in (or later lowering the value)
    does not consume a backlog in one shot.
    """
    if _pruning_disabled(keep):
        return PruneOutcome(deleted=0, deferred=0, existing=0)
    if not rounds_dir.is_dir():
        return PruneOutcome(deleted=0, deferred=0, existing=0)
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
    if _is_bulk(len(stale), keep):
        return PruneOutcome(deleted=0, deferred=len(stale), existing=len(numbered))
    for _num, old in stale:
        old.unlink(missing_ok=True)
    return PruneOutcome(deleted=len(stale), deferred=0, existing=len(numbered))


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
