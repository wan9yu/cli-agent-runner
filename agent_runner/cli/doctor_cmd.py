"""doctor subcommand — never launches the configured agent; runs the same
read-only-ish boot checks serve does (it may create/probe the log dir to
verify it's writable), then reports window overlaps + phase plan."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner import _plugin_manifest, phase_select, startup_check
from agent_runner.cli.common import cfg_from_args, emit


@dataclass(frozen=True)
class DoctorReport:
    checks: list[startup_check.CheckResult]
    overlaps: list[phase_select.WindowOverlap]
    plan: list[dict]
    cgroup: dict
    sigterm_grace: dict


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
                "resume_at": sel.resume_at.isoformat() if sel.resume_at else None,
                "resume_timezone": sel.resume_timezone,
                "skipped": sel.skipped,
                "active_window": sel.active_window,
            }
        )
    return out


def _cgroup_report(cfg) -> dict:
    """Read-only cgroup delegation-readiness snapshot for `doctor` -- never
    emits an event (unlike serve's `_probe_and_emit_cgroup_defer`, which
    shares the same underlying `metrics` probes but writes
    `host_cgroup_memory_limit`). `doctor` never launches the agent or writes
    anything; this section is the same discipline applied to cgroup state."""
    from agent_runner import metrics
    from agent_runner.cli._serve_cgroup import brake_report_state

    limits = metrics.cgroup_memory_limits()
    delegated = metrics.cgroup_delegated(self_cgroup=limits["cgroup_path"])
    return {
        "cgroup_path": limits["cgroup_path"],
        "memory_max": limits["memory_max"],
        "memory_high": metrics.cgroup_memory_high(self_cgroup=limits["cgroup_path"]),
        "delegated": delegated,
        "brake": brake_report_state(cfg.monitor.host_health.brake.memory_high, delegated),
    }


def cmd_doctor(args) -> int:
    cfg = cfg_from_args(args)
    is_cooperative = _plugin_manifest.is_cooperative_agent(cfg.agent.binary)
    sigterm_grace = {
        "agent": cfg.agent.binary,
        "cooperative": is_cooperative,
        "resolved_s": _plugin_manifest.resolve_sigterm_grace_s(
            cfg.agent.binary, cfg.agent.sigterm_grace_s
        ),
    }
    report = DoctorReport(
        checks=startup_check.run_battery(cfg),
        overlaps=phase_select.find_phase_window_overlaps(cfg),
        plan=_plan(cfg, args.rounds),
        cgroup=_cgroup_report(cfg),
        sigterm_grace=sigterm_grace,
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
        if step["paused"]:
            where = "paused"
            if step["resume_at"] is not None:
                where += f" -> resumes {step['resume_phase']} at {step['resume_at']}"
        else:
            where = step["phase"] or "base"
        lines.append(f"  round {step['round']}: {where}")
    cooperative = _plugin_manifest.cooperative_manifest_names()
    lines.append(f"cooperative presets: {', '.join(sorted(cooperative)) or '(none)'}")
    sg = report.sigterm_grace
    lines.append(
        f"sigterm grace: {sg['resolved_s']}s "
        f"({'cooperative' if sg['cooperative'] else 'non-cooperative'}, agent={sg['agent']})"
    )
    cgroup = report.cgroup
    lines.append("cgroup:")
    lines.append(f"  cgroup_path: {cgroup['cgroup_path']}")
    lines.append(f"  memory_max: {cgroup['memory_max']}")
    lines.append(f"  memory_high: {cgroup['memory_high']}")
    lines.append(f"  delegated: {cgroup['delegated']}")
    lines.append(f"  brake: {cgroup['brake']}")
    # Approximates serve's own advisory (_serve_cgroup._probe_and_emit_cgroup_defer):
    # only surface the "soft-brake is inert" hint when it could actually apply --
    # the brake is enabled (report.cgroup["brake"] != "off") or a memory_high/
    # memory_max limit is already bound. Otherwise the brake is at its default
    # (off) with nothing bound, and the hint would contradict "brake: off" above.
    # Coarser than serve's own_scope (which additionally requires the bound to
    # originate from the caller's OWN leaf, not an inherited ancestor) -- doctor's
    # _cgroup_report never tracks the bounding cgroup path, so this errs toward
    # showing the readiness hint in the ancestor-bound case.
    if cgroup["delegated"] is False and (
        cgroup["brake"] != "off"
        or cgroup["memory_high"] is not None
        or cgroup["memory_max"] is not None
    ):
        from agent_runner.cli._serve_cgroup import _UNDELEGATED_HINT

        lines.append(f"  hint: {_UNDELEGATED_HINT}")
    return "\n".join(lines)
