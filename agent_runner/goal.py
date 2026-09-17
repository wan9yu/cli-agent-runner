"""Child-side goal-check executor: each round the supervisor runs the
operator's ``[goal].checks`` and emits one ``goal_check`` event per check.

A bounded, reap-safe wrapper around ``agent_runner._bounded.run_bounded`` --
each check gets its own subprocess, its own ``timeout_s``, and its own event;
a hung or failing check never blocks the round beyond its declared budget.
Kept out of the startup import graph on purpose (see
tests/invariants/test_import_footprint.py): ``runner.py`` imports
``run_goal_checks`` function-scope, behind ``cfg.goal is not None``.
"""

from __future__ import annotations

import math
from pathlib import Path

from agent_runner import events
from agent_runner._bounded import run_bounded
from agent_runner.config import GoalConfig
from agent_runner.events import emit


def _last_float_token(stdout: str) -> float | None:
    """The last whitespace-separated token of ``stdout`` that parses as a
    FINITE float, else ``None``. A plain stdout scrape -- not a read of a
    plugin-controlled events-*.jsonl field -- so no ``_coerce_float`` is
    needed here; the treadmill assessor (a later task) reads this field back
    through ``_coerce_float``. ``nan``/``inf`` are rejected the same as an
    unparseable token -- ``nan`` isn't even equal to itself, which would
    break the assessor's "did the value change" comparison."""
    for token in reversed(stdout.split()):
        try:
            value = float(token)
        except ValueError:
            continue
        if math.isfinite(value):
            return value
    return None


def _resolve_check_cwd(check_cwd: str | None, work_dir: Path) -> Path:
    """Resolve a ``[[goal.checks]]`` entry's ``cwd`` against ``work_dir`` (see
    ``_GoalCheckConfig``'s docstring: the check runner resolves it, not the
    config loader). ``/`` keeps an already-absolute ``check_cwd`` as-is and
    expands a leading ``~``; ``None`` falls back to ``work_dir`` itself."""
    if not check_cwd:
        return work_dir
    return work_dir / Path(check_cwd).expanduser()


def run_goal_checks(
    cfg_goal: GoalConfig,
    *,
    work_dir: Path,
    log_dir: Path,
    dry_run: bool,
) -> None:
    """Run every ``[[goal.checks]]`` entry, bounded by its own ``timeout_s``,
    and emit one ``goal_check`` event per check. ``dry_run`` emits
    ``{skipped: True}`` for every check and never spawns a subprocess. A
    misconfigured check (missing binary, missing cwd, ...) must not crash the
    round child -- ``run_bounded``'s own ``subprocess.Popen`` call can raise
    OSError (FileNotFoundError/PermissionError/NotADirectoryError) BEFORE its
    own timeout machinery ever engages, so that's caught here and reported as
    an unsatisfied check (advisory-only, never a crash-loop)."""
    for check in cfg_goal.checks:
        if dry_run:
            emit(log_dir, events.GOAL_CHECK, name=check.name, skipped=True)
            continue
        cwd = _resolve_check_cwd(check.cwd, work_dir)
        try:
            result = run_bounded(check.cmd, cwd=cwd, timeout_s=check.timeout_s)
        except OSError as exc:
            emit(
                log_dir,
                events.GOAL_CHECK,
                name=check.name,
                satisfied=False,
                value=None,
                timed_out=False,
                skipped=False,
                error=str(exc)[:200],
            )
            continue
        emit(
            log_dir,
            events.GOAL_CHECK,
            name=check.name,
            satisfied=result.rc == 0 and not result.timed_out,
            value=_last_float_token(result.stdout),
            timed_out=result.timed_out,
            skipped=False,
        )
