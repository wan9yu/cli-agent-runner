"""Structured catalog of supervisor defenses.

Each defense is a tuple of (current value, what historical incident it codifies,
which invariant test guards it, current health). This is the single source of
truth — peek/status/start banner all import from here.

Adding a new defense = one entry here + auto-surfaces everywhere via the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runner import events
from agent_runner.config import Config


@dataclass(frozen=True)
class Defense:
    name: str
    value: Any
    codifies: str | None
    guarded_by: Path | None
    current_state: str  # "active" | "degraded" | "off"


def catalog(cfg: Config) -> list[Defense]:
    """Return the defense catalog parameterised by current config."""
    return [
        Defense(
            name="round_timeout_s",
            value=cfg.runtime.round_timeout_s,
            codifies="R1128 — TaskOutput polling loop 60min, scheduler grace fails to trigger",
            guarded_by=Path("tests/unit/test_agent_runtime.py"),
            current_state="active",
        ),
        Defense(
            name="process_group_isolation",
            value="start_new_session=True",
            codifies="#307 — process group reaping for descendant cleanup",
            guarded_by=Path("tests/unit/test_agent_runtime.py"),
            current_state="active",
        ),
        Defense(
            name="sigterm_reaper",
            value=(
                "serve exits only after the current round completes; systemd "
                "KillSignal=SIGTERM + default KillMode=control-group reaches the "
                "whole cgroup, TimeoutStopSec=max(round_timeout)+60"
            ),
            codifies="R725 — SIGTERM-during-round dual-claude race",
            guarded_by=Path("tests/integration/test_serve_loop.py"),
            current_state="active",
        ),
        Defense(
            name="orphan_stash_idempotency_s",
            value=cfg.vcs.stash_idempotency_s,
            codifies="R820 — same-second 3 phantom stashes",
            guarded_by=Path("tests/unit/test_vcs_state.py"),
            current_state="active",
        ),
        Defense(
            name="sha_locked_stash",
            value="drop/pop accept SHA only",
            codifies="§9 IMMUTABLE — batch drop by index breaks under concurrent stash",
            guarded_by=Path("tests/invariants/test_stash_uses_sha_not_index.py"),
            current_state="active",
        ),
        Defense(
            name="set_diff_classification",
            value="no unified-diff +/- line parsing anywhere in agent_runner/",
            codifies="R2110 — rotation-only diff via +-line scan misclassifies",
            guarded_by=Path("tests/invariants/test_set_diff_for_auto_tool_classification.py"),
            current_state="active",
        ),
        Defense(
            name="critical_envs_injection",
            value=sorted(cfg.agent.env.keys()),
            codifies=(
                "Env injection via [agent.env] block — preset-supplied per CLI "
                "(e.g. DISABLE_AUTOUPDATER for claude prevents mid-loop self-updates)"
            ),
            guarded_by=Path("tests/unit/test_agent_runtime.py"),
            current_state="active" if cfg.agent.env else "off",
        ),
        Defense(
            name="startup_smoke_check",
            value="6 checks (config / log_dir / agent_cli / git / prompt_file / prompt_smoke)",
            codifies=(
                "R721 + #446 — _common.md frontmatter caused 4h/123-round silent burn; "
                "now halts serve (config_broken) instead of respawning a broken config"
            ),
            guarded_by=Path("tests/unit/test_serve_config_broken.py"),
            current_state="active",
        ),
        Defense(
            name="crash_loop_breaker",
            value="stop after 5 consecutive short crashes; exp-escalating delay",
            codifies="Run 6 — crashing agent respawned ~100 empty rounds at a fixed 2x delay",
            guarded_by=Path("tests/unit/test_serve_crash_loop.py"),
            current_state="active",
        ),
        Defense(
            name="flock_concurrency",
            value="agent-runner.lock",
            codifies="Architectural — prevent concurrent supervisors corrupting state",
            guarded_by=Path("tests/unit/test_runner.py"),
            current_state="active",
        ),
        Defense(
            name="atomic_state_writes",
            value="tmp + fsync + rename",
            codifies="Data integrity — crashes never leave half-written state files",
            guarded_by=Path("tests/invariants/test_atomic_write_enforced.py"),
            current_state="active",
        ),
        Defense(
            name="event_kind_registry",
            # Read through the module, not `from ... import _BUILTIN_KINDS` — the
            # count must be resolved at call time, not frozen at import.
            value=(
                f"KNOWN_EVENT_KINDS registry view "
                f"({len(events._BUILTIN_KINDS)} built-in kinds + plugin-extensible)"
            ),
            codifies="Prevent events.emit() typos / unregistered kinds slipping past CI",
            guarded_by=Path("tests/invariants/test_event_kind_registry.py"),
            current_state="active",
        ),
    ]
