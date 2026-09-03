"""Round-input assembly + sentinel helpers (0.2.14 Group 4 size-hygiene split).

A distinct axis parked in api.py: prompt assembly, per-phase runtime
resolution, and the self-terminated-sentinel round/status readers. Extracted
purely so api.py stays under the 1000-line module-size gate -- no behavior
change. Every name here is re-exported from ``agent_runner.api`` (bottom
re-export block, same idiom as ``_serve_policy``/``_emit``/``vcs_state``), so
its actual importers (runner/round_log/startup_check/http_progress/serve_cmd)
keep importing from the api facade unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runner.config import Config, RuntimeConfig


def assemble_prompt(
    cfg: Config, phase: str | None, *, context: dict[str, Any] | None = None
) -> str:
    """Assemble the prompt for a given round.

    Resolves per-phase prompt.files override (via PhaseOverride.prompt_files); falls
    back to cfg.prompt.files OR cfg.prompt.file (back-compat single-file). Applies
    concat_separator, optionally strips first-file YAML frontmatter, injects context
    block per cfg.prompt.inject_context + context_injection_mode.

    Returns the assembled prompt text passed to the agent subprocess.
    """
    from agent_runner import prompt_loader

    # Determine files list (per-phase override → global files → single-file fallback)
    files: list[Path]
    prof = cfg.profile_for(phase)
    if prof.prompt_files is not None:
        files = prof.prompt_files
    elif cfg.prompt.files:
        files = cfg.prompt.files
    elif cfg.prompt.file is not None:
        files = [cfg.prompt.file]
    else:
        raise ValueError("no prompt files configured (set prompt.files or prompt.file)")

    # Resolve relative paths against work_dir
    resolved = [f if f.is_absolute() else (cfg.runtime.work_dir / f) for f in files]

    return prompt_loader.assemble_prompt(
        resolved,
        context=context,
        inject_context=cfg.prompt.inject_context,
        mode=cfg.prompt.context_injection_mode,
        concat_separator=cfg.prompt.concat_separator,
        strip_first_frontmatter=cfg.prompt.strip_yaml_frontmatter,
    )


def resolve_runtime_for_phase(cfg: Config, phase_name: str | None) -> RuntimeConfig:
    """Return effective RuntimeConfig for the given phase.

    Thin wrapper over ``cfg.profile_for(phase_name).runtime``. ``None``
    phase_name returns base unchanged. Unknown phase_name silently returns base
    — config-load is responsible for typo catching; this is defensive. Kept as a
    public helper: imported by runner.
    """
    return cfg.profile_for(phase_name).runtime


def read_round_num(log_dir: Path) -> int:
    """Return the most recent round_num from status.json, or 0 if missing/corrupt."""
    from agent_runner.context_store import read_status

    s = read_status(log_dir)
    return s.round_num if s is not None else 0


def read_sentinel_content(log_dir: Path) -> str | None:
    """Return ``log_dir/.agent-done`` content capped at 200 chars, or None if absent."""
    sentinel = log_dir / ".agent-done"
    if not sentinel.exists():
        return None
    try:
        return sentinel.read_text(encoding="utf-8", errors="replace")[:200]
    except OSError:
        return ""


def check_self_terminated_sentinel(log_dir: Path) -> bool:
    """Check for ``log_dir/.agent-done``; emit ``agent_self_terminated`` if present.

    Returns True if sentinel found (caller should stop), False otherwise.
    """
    from agent_runner import events

    reason = read_sentinel_content(log_dir)
    if reason is None:
        return False
    events.emit(log_dir, events.SELF_TERMINATED, reason=reason)
    return True
