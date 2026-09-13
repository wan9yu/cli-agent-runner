"""Event emission wrappers -- plugin-security domain (sandbox trampoline).
Re-exported from ``agent_runner._emit`` (the package facade) -- see its docstring."""

from __future__ import annotations

from pathlib import Path


def emit_plugin_sandbox_kill(
    log_dir: Path, *, hook: str, signal: int, syscall: str | None = None
) -> None:
    """Emit when a trampoline child died by signal (seccomp KILL_PROCESS) -- a
    denied syscall. Distinct from the caller's own hook_failed isolation, which
    also fires: this event names the DENIAL specifically."""
    from agent_runner.events import PLUGIN_SANDBOX_KILL, emit

    emit(log_dir, PLUGIN_SANDBOX_KILL, hook=hook, signal=signal, syscall=syscall)
