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
    sandbox: dict
    plugin_checksums: dict[str, str]


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


def _sandbox_report() -> dict:
    """Achieved-capability summary for the Tier-B (Landlock+seccomp)
    trampoline, so an operator can see what THIS host can actually confine
    before setting ``[plugins] sandbox = "require"``."""
    from agent_runner._sandbox_probe import TIER_B_PROTOCOLS, probe_sandbox_capability

    probe = probe_sandbox_capability()
    return {
        "achieved_tier": probe.achieved_tier,
        "landlock_abi": probe.landlock_abi,
        "seccomp": probe.seccomp,
        "unconfined_reason": probe.unconfined_reason,
        "protocols": list(TIER_B_PROTOCOLS),
    }


def _third_party_plugin_checksums() -> dict[str, str]:
    """Every discovered THIRD-PARTY (non-builtin) plugin's computed sha256,
    keyed by entry-point name — the same name and the same recipe
    ``verify_pin`` checks against, so an operator can copy-paste a printed
    value straight into ``[plugins.pin]`` and have it verify."""
    import agent_runner
    from agent_runner._plugin_checksum import compute_plugin_checksum
    from agent_runner._registry import is_builtin_provenance

    out: dict[str, str] = {}
    for name, value in agent_runner._DISCOVERED_PLUGIN_ENTRIES:
        module_path = agent_runner._entry_point_module_path(value)
        if is_builtin_provenance(name, module_path):
            continue
        try:
            out[name] = compute_plugin_checksum(module_path)
        except Exception as e:  # noqa: BLE001 — doctor reports, never crashes, on a broken plugin
            out[name] = f"<unresolvable: {e}>"
    return out


def cmd_doctor(args) -> int:
    cfg = cfg_from_args(args)
    report = DoctorReport(
        checks=startup_check.run_battery(cfg),
        overlaps=phase_select.find_phase_window_overlaps(cfg),
        plan=_plan(cfg, args.rounds),
        sandbox=_sandbox_report(),
        plugin_checksums=_third_party_plugin_checksums(),
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
    sb = report.sandbox
    tier_detail = f" — {sb['unconfined_reason']}" if sb["unconfined_reason"] else ""
    lines.append(
        f"sandbox: {sb['achieved_tier']}{tier_detail} (confines: {', '.join(sb['protocols'])})"
    )
    if report.plugin_checksums:
        lines.append("third-party plugin checksums (paste into [plugins.pin]):")
        for name, digest in sorted(report.plugin_checksums.items()):
            lines.append(f'  {name} = "{digest}"')
    return "\n".join(lines)
