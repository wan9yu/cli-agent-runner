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


def emit_plugin_builtin_name_squat(log_dir: Path, *, name: str, module_path: str) -> None:
    """Emit when a discovered entry point claims a RESERVED builtin name while
    resolving to a module OUTSIDE the core ``agent_runner.builtin_plugins``
    namespace -- a name-squatter. Builtin trust is denied: the entry falls
    through to the third-party pin/sandbox gate. This event makes the claim
    auditable rather than silent."""
    from agent_runner.events import PLUGIN_BUILTIN_NAME_SQUAT, emit

    emit(log_dir, PLUGIN_BUILTIN_NAME_SQUAT, name=name, module_path=module_path)


def emit_plugin_checksum_mismatch(log_dir: Path, *, name: str, expected: str, actual: str) -> None:
    """Emit when a third-party plugin's computed sha256 doesn't match its
    ``[plugins.pin]`` entry (or the pin is unreadable) -- the loader refuses
    to import that ONE plugin when this fires; it never loads unconfined."""
    from agent_runner.events import PLUGIN_CHECKSUM_MISMATCH, emit

    emit(log_dir, PLUGIN_CHECKSUM_MISMATCH, name=name, expected=expected, actual=actual)


def emit_plugin_sandbox_degraded(
    log_dir: Path, *, requested: str, achieved_tier: str, reason: str
) -> None:
    """Emit when a plugin loads (or would run) at a WEAKER tier than
    ``requested`` -- e.g. an unpinned third-party plugin refused under
    ``sandbox = "require"``, or a ``"prefer"`` load proceeding unconfined."""
    from agent_runner.events import PLUGIN_SANDBOX_DEGRADED, emit

    emit(
        log_dir,
        PLUGIN_SANDBOX_DEGRADED,
        requested=requested,
        achieved_tier=achieved_tier,
        reason=reason,
    )
