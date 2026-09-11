"""doctor subcommand — never launches the configured agent; runs the same
read-only-ish boot checks serve does (it may create/probe the log dir to
verify it's writable), then reports window overlaps + phase plan."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner import phase_select, startup_check
from agent_runner.cli.common import cfg_from_args, emit


@dataclass(frozen=True)
class DoctorReport:
    checks: list[startup_check.CheckResult]
    overlaps: list[phase_select.WindowOverlap]
    plan: list[dict]


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
        plan=_plan(cfg, args.rounds),
    )
    json_mode = getattr(args, "json", False)
    if json_mode:
        emit(report, json_mode=True)
    else:
        print(_format(report))
    return 0


def _format(report: DoctorReport) -> str:
    # Hand-formatted rather than common._pretty: that helper repr()s nested
    # lists/dicts, which would dump report.plan's per-round dicts as Python
    # reprs instead of the readable "round N: phase" lines below.
    lines = ["checks:"]
    for c in report.checks:
        mark = "ok" if c.ok else "FAIL"
        detail = ""
        if not c.ok:
            detail = f" — {c.reason}" + (f" (fix: {c.how_to_fix})" if c.how_to_fix else "")
        lines.append(f"  [{mark}] {c.name}{detail}")
    if report.overlaps:
        lines.append("window overlaps:")
        for o in report.overlaps:
            lines.append(f"  {o.phase_a} ({o.window_a}) vs {o.phase_b} ({o.window_b})")
    lines.append("phase plan:")
    for step in report.plan:
        where = "paused" if step["paused"] else (step["phase"] or "base")
        lines.append(f"  round {step['round']}: {where}")
    return "\n".join(lines)
