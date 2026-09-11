"""doctor subcommand — read-only pre-flight: battery + window overlaps + phase plan."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner import phase_select, startup_check
from agent_runner.cli.common import cfg_from_args, emit


@dataclass(frozen=True)
class DoctorReport:
    checks: list
    overlaps: list
    plan: list


def add_parser(sub, parent) -> None:
    p = sub.add_parser("doctor", parents=[parent], help="Read-only pre-flight: checks + phase plan")
    p.add_argument("--rounds", type=int, default=3, help="Phase-plan preview depth (default 3)")
    p.set_defaults(func=cmd_doctor)


def _plan(cfg, rounds: int) -> list[dict]:
    out = []
    for r in range(1, rounds + 1):
        sel = phase_select.select_phase(cfg, r)
        out.append(
            {
                "round": r,
                "phase": sel.phase,
                "paused": sel.paused,
                "resume_phase": sel.resume_phase,
                "skipped": sel.skipped,
                "active_window": sel.active_window,
            }
        )
    return out


def cmd_doctor(args) -> int:
    cfg = cfg_from_args(args)
    report = DoctorReport(
        checks=startup_check.run_battery(cfg),
        overlaps=phase_select.find_phase_window_overlaps(cfg),
        plan=_plan(cfg, getattr(args, "rounds", 3)),
    )
    json_mode = getattr(args, "json", False)
    if json_mode:
        emit(report, json_mode=True)
    else:
        print(_format(report))
    return 0


def _format(report: DoctorReport) -> str:
    lines = ["checks:"]
    for c in report.checks:
        mark = "ok" if c.ok else "FAIL"
        lines.append(f"  [{mark}] {c.name}" + (f" — {c.reason}" if not c.ok else ""))
    if report.overlaps:
        lines.append("window overlaps:")
        for o in report.overlaps:
            lines.append(f"  {o.phase_a} ({o.window_a}) vs {o.phase_b} ({o.window_b})")
    lines.append("phase plan:")
    for step in report.plan:
        where = "paused" if step["paused"] else (step["phase"] or "base")
        lines.append(f"  round {step['round']}: {where}")
    return "\n".join(lines)
