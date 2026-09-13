"""Per-call Landlock+seccomp trampoline for third-party DirtyHandler (and, once
the spawn seam lands, SpawnHook) calls. The child self-restricts using stdlib +
bindings ONLY, THEN imports the target plugin -- so this module's own package
import must never auto-import plugins (guaranteed by the loader's discover-only
import step). Wire: one bounded JSON line each way; stdout is schema-validated.

The confinement bindings (``py_landlock`` / ``pyseccomp``, the opt-in ``sandbox``
extra) are Linux-only and imported LAZILY inside the child's restrict path --
never at module scope -- so this module imports cleanly on macOS and on a base
Linux install. Enforcement (seccomp KILL_PROCESS, Landlock EACCES) exists only
on Linux; off-Linux the restrict step is a no-op and the confinement guarantee
is absent (the parent refuses ``sandbox=require`` there rather than run a
third-party handler unconfined -- see ``dispatch_dirty``)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

from agent_runner import hooks
from agent_runner.api_types import DirtyOutcome

_TRAMPOLINE_TIMEOUT_S = 30.0  # wall-clock; not config-tunable this release
_MAX_WIRE_BYTES = 64 * 1024
_REF_CAP = 256
_STDERR_CAP = 512

# seccomp KILL_PROCESS deny-list (default-allow). A deny-list, not an allowlist:
# an allowlist is arch/libc-fragile (a libc update adds a new syscall and the
# whole process dies). execve/execveat are NOT here -- a dirty handler shells
# out to git, which needs to exec. Network + ptrace + io_uring + bpf are the
# exfil / escape / sandbox-bypass primitives a dirty handler never needs.
_DIRTY_DENY_SYSCALLS = (
    "socket",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "sendto",
    "recvfrom",
    "sendmsg",
    "recvmsg",
    "getsockopt",
    "setsockopt",
    "socketpair",
    "ptrace",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "bpf",
)


def _sandbox_enforceable() -> bool:
    """Whether Landlock+seccomp confinement can actually engage on this host.

    A cheap, binding-free platform gate (the bindings import stays inside the
    child's restrict path) -- consulted by ``dispatch_dirty`` to fail
    ``sandbox=require`` closed rather than run a third-party handler unconfined.
    """
    return sys.platform == "linux"


# ---------------------------------------------------------------------------
# Wire (context) serialization -- symmetric parent<->child.
# ---------------------------------------------------------------------------


def _ctx_to_wire(ctx: hooks.HookContext) -> dict:
    vcs = ctx.vcs
    return {
        "work_dir": str(ctx.work_dir),
        "log_dir": str(ctx.log_dir),
        "project": ctx.project,
        "round_num": ctx.round_num,
        "phase": ctx.phase,
        "agent_name": ctx.agent_name,
        "agent_binary": ctx.agent_binary,
        "agent_log_path": str(ctx.agent_log_path) if ctx.agent_log_path else None,
        "dry_run": ctx.dry_run,
        "anomaly_repetitive_window": ctx.anomaly_repetitive_window,
        "anomaly_repetitive_threshold": ctx.anomaly_repetitive_threshold,
        "vcs": None
        if vcs is None
        else {"dirty_action": vcs.dirty_action, "stash_idempotency_s": vcs.stash_idempotency_s},
    }


def _ctx_from_wire(data: dict) -> hooks.HookContext:
    vcs = data.get("vcs")
    agent_log_path = data.get("agent_log_path")
    return hooks.HookContext(
        work_dir=Path(data["work_dir"]),
        log_dir=Path(data["log_dir"]),
        project=data["project"],
        round_num=data["round_num"],
        phase=data["phase"],
        agent_name=data["agent_name"],
        agent_binary=data["agent_binary"],
        agent_log_path=Path(agent_log_path) if agent_log_path else None,
        dry_run=data["dry_run"],
        anomaly_repetitive_window=data["anomaly_repetitive_window"],
        anomaly_repetitive_threshold=data["anomaly_repetitive_threshold"],
        vcs=None
        if vcs is None
        else hooks.VcsHookView(
            dirty_action=vcs["dirty_action"], stash_idempotency_s=vcs["stash_idempotency_s"]
        ),
    )


def _resolve_entry(owner: str) -> tuple[str, str]:
    from agent_runner import _DISCOVERED_PLUGIN_ENTRIES

    for name, value in _DISCOVERED_PLUGIN_ENTRIES:
        if name == owner:
            module_path, _, attr_path = value.partition("[")[0].rstrip().partition(":")
            return module_path, attr_path
    raise LookupError(f"no discovered entry point owns {owner!r}")


# ---------------------------------------------------------------------------
# Parent-side launcher.
# ---------------------------------------------------------------------------


def run_hook_sandboxed(
    hook_kind: Literal["spawn_hook", "dirty_handler"],
    owner: str,
    hook_name: str,
    ctx: hooks.HookContext,
    *,
    log_dir: Path,
    dirty_files: list[str] | None = None,
    timeout_s: float = _TRAMPOLINE_TIMEOUT_S,
) -> DirtyOutcome | None:
    """Launch the confinement child for one third-party hook call and return its
    validated outcome. Secrets never cross the wire: only context field NAMES /
    plain values that ``_ctx_to_wire`` chooses are sent. A child killed by signal
    (seccomp KILL_PROCESS) emits ``plugin_sandbox_kill`` and raises; a non-zero
    exit or a timeout raises too -- the caller (``dispatch_dirty``) isolates any
    raise as ``hook_failed``. Both child streams are redact-capped before any
    byte reaches an event."""
    module_path, attr_path = _resolve_entry(owner)
    payload = {
        "schema": "plugin_sandbox_input/1",
        "ctx": _ctx_to_wire(ctx),
        "hook_kind": hook_kind,
        "spawn_view": None,
        "dirty_files": dirty_files,
    }
    import os

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner._plugin_sandbox",
            hook_kind,
            module_path,
            attr_path,
            hook_name,
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=timeout_s,
    )
    if proc.returncode < 0:
        from agent_runner.api import emit_plugin_sandbox_kill

        emit_plugin_sandbox_kill(log_dir, hook=hook_name, signal=-proc.returncode)
        raise RuntimeError(f"trampoline for {hook_name} killed by signal {-proc.returncode}")
    if proc.returncode != 0:
        detail = hooks._cap_redacted(proc.stderr.decode("utf-8", "replace"), _STDERR_CAP)
        raise RuntimeError(f"trampoline for {hook_name} exited {proc.returncode}: {detail}")
    return _parse_dirty_stdout(proc.stdout)


def _parse_dirty_stdout(raw: bytes) -> DirtyOutcome | None:
    if len(raw) > _MAX_WIRE_BYTES:
        raise ValueError("trampoline stdout exceeded wire cap")
    obj = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(obj, dict)
        or set(obj) - {"schema", "kind", "ref"}
        or obj.get("schema") != "dirty_outcome/1"
    ):
        shape = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise ValueError(f"invalid dirty_outcome wire: {shape}")
    kind = obj["kind"]
    if kind not in (None, "stashed", "committed", "ignored"):
        raise ValueError(f"invalid dirty_outcome kind: {kind!r}")
    if kind is None:
        return None
    ref = obj["ref"]
    return DirtyOutcome(
        kind=kind, ref=hooks._cap_redacted(ref, _REF_CAP) if ref is not None else None
    )


# ---------------------------------------------------------------------------
# Child self-restriction (Linux-only; imported lazily; no-op off-Linux).
# ---------------------------------------------------------------------------


def _readable_roots() -> list[str]:
    """Real, existing directory entries of ``sys.path`` plus the interpreter
    prefixes -- captured BEFORE any Landlock ratchet so the plugin (and stdlib)
    can still be imported once confined. Landlock is one-way: once applied, an
    unlisted path is denied for every handled access."""
    import os

    roots: set[str] = set()
    for p in [*sys.path, sys.prefix, sys.base_prefix, sys.exec_prefix]:
        if p and os.path.isdir(p):
            roots.add(os.path.realpath(p))
    return sorted(roots)


def _apply_landlock_dirty(work_dir: Path, log_dir: Path, git_bin: str | None) -> None:
    """DirtyHandler Landlock profile: read the import roots, read-write work_dir
    and log_dir (the handler stashes/commits and calls events.emit), execute the
    resolved git binary, and -- by NOT granting any net rule on a net-capable
    kernel -- deny the network. Best-effort (seccomp is the primary denier): a
    kernel without Landlock, or without the sandbox extra, degrades silently
    here and leaves seccomp to enforce."""
    if sys.platform != "linux":
        return
    try:
        from py_landlock import Landlock, LandlockError
    except ImportError:
        return
    read_roots = _readable_roots()
    try:
        lock = Landlock(strict=False)
        if read_roots:
            lock.allow_read(*read_roots)
        lock.allow_read_write(str(work_dir), str(log_dir))
        if git_bin:
            lock.allow_execute(git_bin)
        lock.apply()
    except LandlockError:
        return


def _apply_seccomp_denylist(syscalls: tuple[str, ...]) -> None:
    """Install the KILL_PROCESS deny-list. Mandatory on Linux: if the binding or
    libseccomp is missing, the ImportError/OSError propagates so the child dies
    before importing the plugin (fail closed -- never run a third-party handler
    with the primary denier absent). No-op off-Linux (no seccomp there)."""
    if sys.platform != "linux":
        return
    import pyseccomp as seccomp

    flt = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in syscalls:
        try:
            flt.add_rule(seccomp.KILL_PROCESS, name)
        except (ValueError, OSError, RuntimeError):
            continue  # syscall unknown on this arch/libseccomp -- best-effort per-name
    flt.load()


def _default_restrict_dirty(ctx: hooks.HookContext) -> None:
    import shutil

    git_bin = shutil.which("git")
    _apply_landlock_dirty(ctx.work_dir, ctx.log_dir, git_bin)
    _apply_seccomp_denylist(_DIRTY_DENY_SYSCALLS)


def _run_dirty_child(
    module_path: str,
    attr_path: str,
    hook_name: str,
    ctx: hooks.HookContext,
    dirty_files: list[str],
    *,
    restrict,
) -> dict:
    """Self-restrict, THEN import the plugin and run its handler. The order is
    load-bearing: the plugin's code executes for the FIRST time already confined.
    Returns the wire dict; ``_main`` writes it to stdout."""
    restrict(ctx)

    import importlib

    mod = importlib.import_module(module_path)
    target = mod
    for attr in filter(None, attr_path.split(".")):
        target = getattr(target, attr)
    handler = next(h for h in target.dirty_handlers if h.name == hook_name)
    outcome = handler.handle_dirty(ctx, dirty_files)
    if outcome is None:
        return {"schema": "dirty_outcome/1", "kind": None, "ref": None}
    return {"schema": "dirty_outcome/1", "kind": outcome.kind, "ref": outcome.ref}


def _main(argv: list[str]) -> int:
    hook_kind, module_path, attr_path, hook_name = argv[1], argv[2], argv[3], argv[4]
    if hook_kind != "dirty_handler":
        raise SystemExit(f"plugin sandbox: unsupported hook_kind {hook_kind!r}")
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    ctx = _ctx_from_wire(data["ctx"])

    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # a plugin's own print() must never corrupt the JSON wire
    try:
        result = _run_dirty_child(
            module_path,
            attr_path,
            hook_name,
            ctx,
            data["dirty_files"],
            restrict=_default_restrict_dirty,
        )
    finally:
        sys.stdout = real_stdout
    real_stdout.write(json.dumps(result))
    real_stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
