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


def emit_plugin_spawn_decision(
    log_dir: Path, *, hook: str, action: str, defer_s: int, reason: str
) -> None:
    """Emit the collapsed pre-spawn verdict the serve admission gate acted on --
    ``defer`` or ``skip`` (a ``proceed`` is the silent default, no event). ``hook``
    is the winning hook by the collapse's own precedence (skip > max-defer)."""
    from agent_runner.events import PLUGIN_SPAWN_DECISION, emit

    emit(log_dir, PLUGIN_SPAWN_DECISION, hook=hook, action=action, defer_s=defer_s, reason=reason)


def emit_plugin_spawn_override_ignored(log_dir: Path, *, hook: str, action: str) -> None:
    """Emit when a spawn hook returned a non-``proceed`` verdict but is NOT in
    ``[plugins] spawn_override_allow`` -- its blocking power is denied (the
    decision is downgraded to ``proceed`` before the collapse). Blocking a round
    is an operator-granted capability, so an un-listed hook's ``defer``/``skip``
    is audited here rather than honored silently."""
    from agent_runner.events import PLUGIN_SPAWN_OVERRIDE_IGNORED, emit

    emit(log_dir, PLUGIN_SPAWN_OVERRIDE_IGNORED, hook=hook, action=action)


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
