"""Architectural invariants.

- serve_cmd.py imports from a strict allowlist (no business logic)
- cli command files call api.X (not direct module imports)
- All api_types are frozen dataclasses
- KNOWN_ALERT_KINDS in monitor.py matches the 13 builtin detectors
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
ALLOWED_SERVE_IMPORTS = {
    "fcntl",
    "os",
    "sys",
    "signal",
}
ALLOWED_SERVE_FROM = [
    ("agent_runner", {"metrics", "phase_select", "schedule"}),
    ("agent_runner._substrate", {"compute_git_head", "compute_paths_hash"}),
    ("agent_runner.hooks", {"run_serve_startup_hooks"}),
    (
        "agent_runner.cli._serve_round",
        {
            "_maybe_emit_recovered",
            "_maybe_pause_for_memory_pressure",
            "_pause_poll",
            "_spawn_round",
            "post_round_verdicts",
        },
    ),
    ("agent_runner.cli._serve_cgroup", {"_probe_and_emit_cgroup_defer"}),
    (
        "agent_runner.api",
        {
            "check_self_terminated_sentinel",
            "emit_config_broken",
            "emit_max_rounds_reached",
            "emit_rate_limit_stop",
            "emit_round_logs_prune_deferred",
            "emit_stop_file_detected",
            "emit_round_substrate_before",
            "emit_round_substrate_after",
            "emit_fresh_eyes_round_triggered",
            "emit_schedule_paused",
            "emit_schedule_phase_skipped",
            "emit_schedule_resumed",
            "outer_round_ceiling_s",
        },
    ),
    ("agent_runner._serve_policy", {"PERMANENT_CONFIG_EXIT"}),
    ("agent_runner.clock", {"SYSTEM_CLOCK", "Clock"}),
    ("agent_runner.cli.common", {"cfg_from_args_or_config_error"}),
    ("agent_runner.lifecycle", {"PIDFile"}),
    (
        "agent_runner.round_log",
        {
            "ROUND_CURRENT_LINK",
            "atomic_relink",
            "next_round_num",
            "prune_old_round_logs",
        },
    ),
    (
        "agent_runner._throttle",
        {
            "_active_throttles",
            "_apply_back_off",
            "_check_throttle_state",
            "_interruptible_sleep",
            "round_outcome",
            "round_was_mem_terminated",
        },
    ),
]

# _serve_round.py (0.2.16 Task 5a: serve logic extracted out of serve_cmd.py
# for LOC headroom — see its own module docstring) mirrors serve_cmd.py's
# import-allowlist treatment: it is the OTHER half of the serve loop's
# business logic, so it gets the same "no unsanctioned imports" scan rather
# than sitting unwatched just because it isn't serve_cmd.py itself.
ALLOWED_SERVE_ROUND_IMPORTS = {
    "os",
    "signal",
    "subprocess",
    "pathlib",
}
ALLOWED_SERVE_ROUND_FROM = [
    ("agent_runner", {"host_health", "metrics"}),
    ("agent_runner.agent_runtime", {"_kill_stray_descendants", "_live_children"}),
    (
        "agent_runner._serve_policy",
        {
            "_MEM_LOOP_PERSIST_THRESHOLD",
            "_MEM_LOOP_PERSIST_WINDOW_S",
            "_NO_PROGRESS_SHORT_S",
            "_ROUND_TERM_GRACE_S",
            "_ROUND_UNREAPED_RC",
            "_mem_loop_decision",
            "_no_progress_decision",
            "post_round_decision",
            "CRASH_LOOP_EXIT",
            "MEM_LOOP_EXIT",
            "MEM_LOOP_PERSISTENT_EXIT",
            "PERMANENT_CONFIG_EXIT",
        },
    ),
    (
        "agent_runner._throttle",
        {"mem_loop_events_in_window", "pending_recovered", "round_had_no_progress"},
    ),
    (
        "agent_runner.api",
        {
            "emit_config_broken",
            "emit_crash_loop",
            "emit_mem_loop",
            "emit_mem_loop_persistent",
            "emit_mem_pressure_deferred_to_cgroup",
            "emit_round_deferred",
            "emit_round_mem_critical_sample",
            "emit_round_mem_terminated",
            "emit_round_resumed",
            "emit_round_supervisor_wedged",
            "emit_stalled_no_progress",
            "emit_transient_error_recovered",
        },
    ),
    (
        "agent_runner.cli._serve_cgroup",
        {"_emit_round_cgroup_memory", "_maybe_emit_oom_killed", "_stash_round_cgroup_state"},
    ),
    ("agent_runner.clock", {"SYSTEM_CLOCK", "Clock"}),
]

# _serve_cgroup.py (0.2.19: carved out of _serve_round.py to lift the
# cgroup-pressure spine into its own module) gets the same "no unsanctioned
# imports" scan -- see ALLOWED_SERVE_ROUND_* above for the same treatment on
# its sibling. Importing zero agent_runner.cli.* modules here is the
# structural half of the one-way-import proof (the other half is the clean-
# interpreter import check in the carve commit).
ALLOWED_SERVE_CGROUP_IMPORTS = {"sys"}
ALLOWED_SERVE_CGROUP_FROM = [
    ("agent_runner", {"metrics"}),
    ("agent_runner._serve_policy", {"_ROUND_UNREAPED_RC"}),
    (
        "agent_runner.api",
        {"emit_host_cgroup_memory_limit", "emit_round_cgroup_memory", "emit_round_oom_killed"},
    ),
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


def _assert_imports_within_allowlist(
    file: Path,
    *,
    label: str,
    allowed_plain: set[str],
    allowed_from: list[tuple[str, set[str]]],
    plain_exceptions: set[str] = frozenset(),
) -> None:
    plain, froms = _imports_in(file)
    bad_plain = plain - allowed_plain - plain_exceptions
    assert not bad_plain, f"{label} has unsanctioned imports: {bad_plain}"
    for mod, names in froms:
        if mod.startswith("agent_runner"):
            allowed = next((n for m, n in allowed_from if m == mod), None)
            assert allowed is not None, f"{label} imports {mod} (not in allowlist)"
            extra = names - allowed
            assert not extra, f"{label} imports {extra} from {mod} (not allowed)"


def test_serve_cmd_should_stay_within_import_allowlist_when_scanned() -> None:
    _assert_imports_within_allowlist(
        PKG / "cli/serve_cmd.py",
        label="serve_cmd",
        allowed_plain=ALLOWED_SERVE_IMPORTS,
        allowed_from=ALLOWED_SERVE_FROM,
        plain_exceptions={"agent_runner.cli", "agent_runner.cli.common", "agent_runner.lifecycle"},
    )


def test_serve_round_should_stay_within_import_allowlist_when_scanned() -> None:
    _assert_imports_within_allowlist(
        PKG / "cli/_serve_round.py",
        label="_serve_round",
        allowed_plain=ALLOWED_SERVE_ROUND_IMPORTS,
        allowed_from=ALLOWED_SERVE_ROUND_FROM,
    )


def test_serve_cgroup_should_stay_within_import_allowlist_when_scanned() -> None:
    _assert_imports_within_allowlist(
        PKG / "cli/_serve_cgroup.py",
        label="_serve_cgroup",
        allowed_plain=ALLOWED_SERVE_CGROUP_IMPORTS,
        allowed_from=ALLOWED_SERVE_CGROUP_FROM,
    )


_EMIT_SUBMODULE_RE = re.compile(r"agent_runner\._emit\.\w+")


def _direct_emit_submodule_imports(base: Path) -> list[str]:
    """Every ``*.py`` file under ``base`` that imports a real
    ``agent_runner._emit.<submodule>`` module (``from ... import`` or plain
    ``import``), as ``"<path>: <offending source line>"`` strings. AST-based,
    not text/regex over the raw source, so a `patch("agent_runner._emit.emit_
    foo")` STRING (a legitimate, common test pattern) is never mistaken for a
    real import -- only nodes the parser itself classifies as Import/ImportFrom
    are considered."""
    offenders: list[str] = []
    for f in sorted(base.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        if f.parts[-2:] == ("_emit", "__init__.py"):
            # The facade itself: importing each submodule to re-export it IS
            # the one sanctioned crossing point (see its own docstring).
            continue
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _EMIT_SUBMODULE_RE.fullmatch(node.module)
            ):
                offenders.append(f"{f}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _EMIT_SUBMODULE_RE.fullmatch(alias.name):
                        offenders.append(f"{f}: import {alias.name}")
    return offenders


def test_emit_facade_rule_should_have_no_direct_submodule_imports_when_scanned() -> None:
    """``_emit/__init__.py``'s docstring says every consumer imports from the
    FACADE (``agent_runner._emit``), never a submodule
    (``agent_runner._emit.<name>``) directly -- until now that rule was prose
    only. A direct submodule import would silently defang a
    `patch("agent_runner._emit.emit_...")`
    test target aimed at the facade: the patch rewrites the facade module's
    attribute, but a name already bound from the submodule directly never
    sees it -- the exact failure shape the 0.2.18 ``round_outcome`` seam hit
    in a different module. Scans both ``agent_runner/`` (production) and
    ``tests/`` (so a test file introducing the bad pattern trips this too)."""
    repo_root = PKG.parent

    offenders = _direct_emit_submodule_imports(PKG) + _direct_emit_submodule_imports(
        repo_root / "tests"
    )

    assert offenders == [], "direct agent_runner._emit.<submodule> import(s) found:\n" + "\n".join(
        offenders
    )


def test_direct_emit_submodule_import_should_be_flagged_when_scanned(tmp_path: Path) -> None:
    """Non-vacuity proof for the scan above: a real submodule import must be
    caught, a facade import and a same-looking STRING must not be."""
    (tmp_path / "bad_from.py").write_text("from agent_runner._emit.rounds import emit_round_end\n")
    (tmp_path / "bad_plain.py").write_text("import agent_runner._emit.serve\n")
    (tmp_path / "good_facade.py").write_text("from agent_runner._emit import emit_round_end\n")
    (tmp_path / "good_string.py").write_text(
        "from unittest.mock import patch\n"
        "def f():\n"
        '    with patch("agent_runner._emit.emit_round_end"):\n'
        "        pass\n"
    )

    offenders = _direct_emit_submodule_imports(tmp_path)

    assert any("bad_from.py" in o and "agent_runner._emit.rounds" in o for o in offenders)
    assert any("bad_plain.py" in o and "agent_runner._emit.serve" in o for o in offenders)
    assert not any("good_facade.py" in o for o in offenders)
    assert not any("good_string.py" in o for o in offenders)
    assert len(offenders) == 2


def test_cli_cmd_files_should_call_api_not_runner_directly_when_scanned() -> None:
    """Each cli/*_cmd.py (except round_cmd, serve_cmd) should import from agent_runner.api."""
    offenders: list[str] = []
    scanned = 0
    for f in (PKG / "cli").glob("*_cmd.py"):
        # round/serve/events run the loop directly; migrate rewrites config file
        # text on disk and never touches the api/runner surface.
        if f.name in ("round_cmd.py", "serve_cmd.py", "events_cmd.py", "migrate_cmd.py"):
            continue
        scanned += 1
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

    assert scanned > 0, "no cli/*_cmd.py files scanned"  # vacuity-guard
    assert offenders == [], f"cli cmd files not calling api.X: {offenders}"


def test_api_types_should_all_be_frozen_dataclasses_when_inspected() -> None:
    import dataclasses
    import inspect

    from agent_runner import api_types

    # Discovered by scanning the module rather than a hand-maintained name
    # list: a newly added dataclass is caught automatically instead of
    # silently escaping this check.
    classes = [
        obj
        for _, obj in inspect.getmembers(api_types, dataclasses.is_dataclass)
        if inspect.isclass(obj) and obj.__module__ == api_types.__name__
    ]

    assert len(classes) > 0, "no dataclasses discovered in agent_runner.api_types"
    for cls in classes:
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} not frozen"


def test_known_alert_kinds_should_be_well_formed_when_inspected() -> None:
    from agent_runner.monitor import KNOWN_ALERT_KINDS

    assert len(KNOWN_ALERT_KINDS) == 13
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in KNOWN_ALERT_KINDS)
