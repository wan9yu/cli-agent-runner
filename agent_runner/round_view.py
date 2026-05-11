"""Build RoundView snapshots for peek's --round / --log / --events drill-down.

Kept separate from api.py and monitor.py so neither approaches its LOC cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runner.api_types import RoundView


def resolve_round_arg(arg: int | str | None, log_dir: Path) -> int | None:
    """Resolve --round value (int, int-string, or 'latest') to a concrete number."""
    if arg is None:
        return None
    if isinstance(arg, int):
        return arg
    if arg == "latest":
        rounds_dir = log_dir / "rounds"
        if not rounds_dir.is_dir():
            return None
        nums: list[int] = []
        for f in rounds_dir.glob("R*-*.log"):
            try:
                nums.append(int(f.name.split("-", 1)[0][1:]))
            except (ValueError, IndexError):
                continue
        return max(nums) if nums else None
    try:
        return int(arg)
    except ValueError as e:
        raise KeyError(f"--round expects int or 'latest', got {arg!r}") from e


def build_round_view(
    log_dir: Path,
    round_num: int,
    events: list[dict[str, Any]],
    *,
    want_log: bool = False,
    tail_lines: int = 50,
) -> RoundView | None:
    """Construct a RoundView for ``round_num`` by reading rounds/R{N}-*.log + events.

    Returns None when the round log file is absent (round never started or already pruned).
    """
    rounds_dir = log_dir / "rounds"
    log_path = next(rounds_dir.glob(f"R{round_num}-*.log"), None)
    if log_path is None:
        return None
    started_at = ""
    phase: str | None = None
    duration: float | None = None
    exit_code: int | None = None
    timed_out: bool | None = None
    for e in events:
        if e.get("round_num") != round_num:
            continue
        kind = e.get("event")
        if kind == "round_start":
            started_at = e.get("ts", "")
            phase = e.get("phase")
        elif kind == "agent_exit":
            duration = e.get("duration_s")
            exit_code = e.get("exit_code")
            timed_out = e.get("timed_out")
    log_tail: str | None = None
    if want_log:
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            log_tail = "\n".join(lines[-tail_lines:])
        except FileNotFoundError:
            log_tail = None
    return RoundView(
        round_num=round_num,
        phase=phase,
        started_at=started_at,
        duration_so_far_s=duration,
        pid=None,
        exit_code=exit_code,
        timed_out=timed_out,
        log_path=log_path,
        log_tail=log_tail,
    )
