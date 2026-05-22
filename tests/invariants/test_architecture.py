"""Architectural invariants.

- serve_cmd.py imports from a strict allowlist (no business logic)
- cli command files call api.X (not direct module imports)
- All api_types are frozen dataclasses
- KNOWN_ALERT_KINDS in monitor.py matches the 11 builtin detectors
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
ALLOWED_SERVE_IMPORTS = {
    "argparse",
    "os",
    "sys",
    "signal",
    "subprocess",
    "time",
    "pathlib",
    "agent_runner",  # only sub-imports below
}
ALLOWED_SERVE_FROM = [
    ("agent_runner._substrate", {"compute_git_head", "compute_paths_hash"}),
    ("agent_runner.hooks", {"run_serve_startup_hooks"}),
    (
        "agent_runner.api",
        {
            "check_self_terminated_sentinel",
            "emit_max_rounds_reached",
            "emit_rate_limit_stop",
            "emit_stop_file_detected",
            "emit_round_substrate_before",
            "emit_round_substrate_after",
            "emit_fresh_eyes_round_triggered",
        },
    ),
    ("agent_runner.cli.common", {"cfg_from_args"}),
    ("agent_runner.lifecycle", {"PIDFile", "send_signal_to_pid"}),
    (
        "agent_runner.round_log",
        {"ROUND_CURRENT_LINK", "atomic_relink", "next_round_num", "prune_old_round_logs"},
    ),
    ("agent_runner._throttle", {"_check_throttle_state", "reset_counters"}),
    ("agent_runner.runner", {"_apply_back_off"}),
]


def _imports_in(file: Path) -> tuple[set[str], list[tuple[str, set[str]]]]:
    tree = ast.parse(file.read_text())
    plain: set[str] = set()
    from_imports: list[tuple[str, set[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                plain.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                from_imports.append((node.module, {a.name for a in node.names}))
    return plain, from_imports


def test_given_serve_cmd_when_imports_scanned_then_within_allowlist() -> None:
    plain, froms = _imports_in(PKG / "cli/serve_cmd.py")
    bad_plain = (
        plain
        - ALLOWED_SERVE_IMPORTS
        - {"agent_runner.cli", "agent_runner.cli.common", "agent_runner.lifecycle"}
    )
    assert not bad_plain, f"serve_cmd has unsanctioned imports: {bad_plain}"
    for mod, names in froms:
        if mod.startswith("agent_runner"):
            allowed = next((n for m, n in ALLOWED_SERVE_FROM if m == mod), None)
            assert allowed is not None, f"serve_cmd imports {mod} (not in allowlist)"
            extra = names - allowed
            assert not extra, f"serve_cmd imports {extra} from {mod} (not allowed)"


def test_given_cli_cmd_files_when_scanned_then_call_api_not_runner_directly() -> None:
    """Each cli/*_cmd.py (except round_cmd, serve_cmd) should import from agent_runner.api."""
    offenders: list[str] = []
    for f in (PKG / "cli").glob("*_cmd.py"):
        if f.name in ("round_cmd.py", "serve_cmd.py", "events_cmd.py"):
            continue
        text = f.read_text()
        # Accept "from agent_runner import api" (standalone or merged with other names)
        # and "from agent_runner.api" / "import agent_runner.api" import forms.
        has_api_import = (
            re.search(r"from agent_runner import [^#\n]*\bapi\b", text) is not None
            or "from agent_runner.api" in text
            or "import agent_runner.api" in text
        )
        if not has_api_import:
            offenders.append(f.name)
    assert offenders == [], f"cli cmd files not calling api.X: {offenders}"


def test_given_api_types_when_inspected_then_all_frozen_dataclasses() -> None:
    import dataclasses

    from agent_runner import api_types

    cls_names = [
        "Alert",
        "InitResult",
        "InstallResult",
        "ProjectState",
        "RoundView",
        "ServiceStatus",
        "SystemMetrics",
    ]
    for name in cls_names:
        cls = getattr(api_types, name)
        assert dataclasses.is_dataclass(cls), f"{name} not a dataclass"
        assert cls.__dataclass_params__.frozen, f"{name} not frozen"


def test_given_known_alert_kinds_when_inspected_then_matches_twelve_detectors() -> None:
    from agent_runner.monitor import KNOWN_ALERT_KINDS

    expected = {
        "timeout_rate",
        "hung",
        "orphan_chain",
        "disk_warning",
        "disk_critical",
        "mem_pressure",
        "smoke_fail_rate",
        "oauth_fail",
        "network_fail",
        "rate_limit_active",
        "anomaly_repetitive_active",
        "supervisor_stale",
    }
    assert KNOWN_ALERT_KINDS == expected
