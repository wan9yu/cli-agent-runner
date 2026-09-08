"""Invariant: `agent-runner serve`'s startup import graph stays lean.

0.2.18 measured `import agent_runner.cli` (what `serve`/`round` execute) pulling
in modules with no consumer on the default config path: `hashlib` (libcrypto,
mapped even though the prompt is only hashed when a pre-round hook is
configured), `zoneinfo` (only `[schedule]`/`now_in_zone` need it), and
`importlib.metadata` (`email`/`zipfile`/`csv` et al — replaced by a direct
`entry_points.txt` scan). Two checks, both deterministic and RSS-free:

1. A clean-subprocess ``sys.modules`` diff — the forbidden set must be absent,
   and the ``agent_runner.*`` modules actually loaded must equal a frozen
   allowlist (so a NEW eager import trips this even if it isn't "forbidden").
2. An AST twin over the modules that were made lazy — no module-level
   ``import``/``from`` of a forbidden module (function-scoped imports are
   fine); catches the regression at the source line without running anything.

See ``docs/internal/notes/2026-09-06-0.2.18-footprint-measurement.md`` §7(a)
for the measurements and design behind this gate.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

# Heavy modules with no consumer on `agent-runner serve`'s default config path.
# The first group (hashlib/zoneinfo + importlib.metadata's tail) are modules
# 0.2.18 actively removed from the startup graph; the rest were already absent
# and are locked in here so a future addition (0.3's new plugin/module) trips
# this gate instead of silently re-bloating the supervisor.
FORBIDDEN_AT_STARTUP = frozenset(
    {
        # removed in 0.2.18
        "hashlib",
        "_hashlib",
        "zoneinfo",
        "_zoneinfo",
        "importlib.metadata",
        "email",
        "zipfile",
        "csv",
        # already absent — locked in
        "logging",
        "http",
        "ssl",
        "urllib.request",
        "asyncio",
        "concurrent.futures",
        "multiprocessing",
        "unittest",
        "xml",
        "agent_runner.http_progress",
        "agent_runner.remote_relay",
        "agent_runner.round_view",
        "agent_runner._docgen",
        "agent_runner.prompt_loader",
        "agent_runner.presets",
    }
)

# The complete set of agent_runner.* modules `import agent_runner.cli` loads
# today. Frozen: growing this list requires editing it with a reason, the
# same idiom as test_architecture.ALLOWED_SERVE_FROM — so a NEW eager import
# (a module that isn't individually "forbidden" above) still trips the gate.
EXPECTED_STARTUP_PKG_MODULES = frozenset(
    {
        "agent_runner",
        "agent_runner._emit",
        "agent_runner._emit.agent",
        "agent_runner._emit.memory",
        "agent_runner._emit.rounds",
        "agent_runner._emit.serve",
        "agent_runner._monitor_detectors",
        "agent_runner._monitor_registry",
        "agent_runner._monitor_state",
        "agent_runner._plugin_scan",
        "agent_runner._redact",
        "agent_runner._registry",
        "agent_runner._resolve",
        "agent_runner._round_outcome",
        "agent_runner._round_support",
        "agent_runner._serve_policy",
        "agent_runner._substrate",
        "agent_runner._throttle",
        "agent_runner._version",
        "agent_runner.agent_runtime",
        "agent_runner.api",
        "agent_runner.api_types",
        "agent_runner.builtin_plugins",
        "agent_runner.builtin_plugins._constants",
        "agent_runner.builtin_plugins.claude_rate_limit",
        "agent_runner.builtin_plugins.codewhale",
        "agent_runner.builtin_plugins.default_dirty_handler",
        "agent_runner.builtin_plugins.gemini",
        "agent_runner.builtin_plugins.kimi",
        "agent_runner.builtin_plugins.pi",
        "agent_runner.cli",
        "agent_runner.cli._serve_cgroup",
        "agent_runner.cli._serve_round",
        "agent_runner.cli.common",
        "agent_runner.cli.events_cmd",
        "agent_runner.cli.init_cmd",
        "agent_runner.cli.install_cmd",
        "agent_runner.cli.migrate_cmd",
        "agent_runner.cli.monitor_cmd",
        "agent_runner.cli.peek_cmd",
        "agent_runner.cli.round_cmd",
        "agent_runner.cli.serve_cmd",
        "agent_runner.cli.service_cmd",
        "agent_runner.cli.upgrade_cmd",
        "agent_runner.clock",
        "agent_runner.config",
        "agent_runner.config.errors",
        "agent_runner.config.loader",
        "agent_runner.config.models",
        "agent_runner.config.parsers",
        "agent_runner.config.validators",
        "agent_runner.context_store",
        "agent_runner.defenses",
        "agent_runner.events",
        "agent_runner.hooks",
        "agent_runner.host_health",
        "agent_runner.lifecycle",
        "agent_runner.metrics",
        "agent_runner.migrations",
        "agent_runner.monitor",
        "agent_runner.phase_select",
        "agent_runner.round_log",
        "agent_runner.runner",
        "agent_runner.scaffold",
        "agent_runner.schedule",
        "agent_runner.service_unit",
        "agent_runner.startup_check",
        "agent_runner.vcs_state",
    }
)

# Files that had a module-level import of a forbidden module removed in
# 0.2.18 — the AST twin guards these against a re-added top-level import.
LAZY_MODULES = ("runner.py", "clock.py", "schedule.py", "_substrate.py")

# _SUBPROCESS_PROBE diffs sys.modules BEFORE vs AFTER the import, rather than
# asserting on the raw post-import set, so a module a .pth file (e.g.
# coverage's subprocess auto-start under `--cov`) preloads before the probe
# even runs is never blamed on agent_runner's own import graph — the
# measurement note flags this as the flake agent-runner's CI matrix would
# otherwise hit.
_SUBPROCESS_PROBE = (
    "import sys\n"
    "before = set(sys.modules)\n"
    "import agent_runner.cli\n"
    "after = sorted(set(sys.modules) - before)\n"
    "print('\\n'.join(after))\n"
)


def _startup_modules() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_forbidden_modules_absent_from_startup_import() -> None:
    loaded = set(_startup_modules())
    present = sorted(loaded & FORBIDDEN_AT_STARTUP)
    assert FORBIDDEN_AT_STARTUP  # vacuity-guard
    assert not present, f"forbidden modules eagerly imported by `agent_runner.cli`: {present}"


def test_startup_pkg_modules_match_frozen_allowlist() -> None:
    loaded = set(_startup_modules())
    pkg_loaded = {m for m in loaded if m == "agent_runner" or m.startswith("agent_runner.")}
    missing = EXPECTED_STARTUP_PKG_MODULES - pkg_loaded
    extra = pkg_loaded - EXPECTED_STARTUP_PKG_MODULES
    assert EXPECTED_STARTUP_PKG_MODULES  # vacuity-guard
    assert not missing and not extra, (
        f"agent_runner.* startup import graph drifted from the frozen allowlist "
        f"(missing={sorted(missing)}, extra={sorted(extra)}). If this is an "
        f"intentional new startup dependency, update EXPECTED_STARTUP_PKG_MODULES "
        f"with a reason."
    )


def _toplevel_forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in tree.body:  # module level only — function-scoped imports are fine
        if isinstance(node, ast.Import):
            offenders += [
                f"{path.name}: import {a.name}"
                for a in node.names
                if a.name.split(".")[0] in FORBIDDEN_AT_STARTUP or a.name in FORBIDDEN_AT_STARTUP
            ]
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_AT_STARTUP or node.module in FORBIDDEN_AT_STARTUP:
                offenders.append(f"{path.name}: from {node.module} import ...")
    return offenders


def test_lazy_modules_have_no_toplevel_forbidden_import() -> None:
    offenders: list[str] = []
    for name in LAZY_MODULES:
        offenders += _toplevel_forbidden_imports(PKG / name)
    assert LAZY_MODULES  # vacuity-guard
    assert not offenders, f"forbidden top-level imports reintroduced: {offenders}"
