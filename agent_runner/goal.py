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

from pathlib import Path

from agent_runner import events
from agent_runner._bounded import run_bounded
from agent_runner.config import GoalConfig
from agent_runner.events import emit


def _last_float_token(stdout: str) -> float | None:
    """The last whitespace-separated token of ``stdout`` that parses as a
    float, else ``None``. A plain stdout scrape -- not a read of a
    plugin-controlled events-*.jsonl field -- so no ``_coerce_float`` is
    needed here; the treadmill assessor (a later task) reads this field back
    through ``_coerce_float``."""
    for token in reversed(stdout.split()):
        try:
            return float(token)
        except ValueError:
            continue
    return None


def run_goal_checks(
    cfg_goal: GoalConfig,
    *,
    work_dir: Path,
    log_dir: Path,
    dry_run: bool,
) -> None:
    """Run every ``[[goal.checks]]`` entry, bounded by its own ``timeout_s``,
    and emit one ``goal_check`` event per check. ``dry_run`` emits
    ``{skipped: True}`` for every check and never spawns a subprocess."""
    for check in cfg_goal.checks:
        if dry_run:
            emit(log_dir, events.GOAL_CHECK, name=check.name, skipped=True)
            continue
        cwd = Path(check.cwd) if check.cwd else work_dir
        result = run_bounded(check.cmd, cwd=cwd, timeout_s=check.timeout_s)
        emit(
            log_dir,
            events.GOAL_CHECK,
            name=check.name,
            satisfied=result.rc == 0 and not result.timed_out,
            value=_last_float_token(result.stdout),
            timed_out=result.timed_out,
            skipped=False,
        )
