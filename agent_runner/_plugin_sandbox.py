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
from typing import Callable, Literal

from agent_runner import _notify, hooks
from agent_runner._procwait import wait_exit
from agent_runner._registry import resolve_entry_target
from agent_runner.api_types import DirtyOutcome, SpawnDecision
from agent_runner.clock import SYSTEM_CLOCK

_TRAMPOLINE_TIMEOUT_S = 30.0  # wall-clock; not config-tunable this release
_MAX_WIRE_BYTES = 64 * 1024
_MAX_STDERR_BYTES = 8 * 1024  # hard read cap; a runaway child can't OOM the supervisor
_REF_CAP = 256
_STDERR_CAP = 512

# The child's environment is built default-DENY from this allowlist -- NOT copied
# from the parent's, which holds the real agent secrets (ANTHROPIC_*, and every
# *_API_KEY/*_TOKEN/*_SECRET agent_runtime injects). A confined plugin is Python
# code: with the full env it could read a secret VALUE from os.environ and persist
# it to the rw work_dir, commit it via the allowed git exec, or return a short one
# in ``ref`` -- exfil seccomp's network block would never see. So only the vars git
# and the Python child genuinely need cross the boundary; anything unlisted drops.
_ENV_ALLOW_EXACT = frozenset({"PATH", "HOME", "LANG", "TMPDIR", "TZ", "VIRTUAL_ENV", "PYTHONPATH"})
_ENV_ALLOW_PREFIX = ("LC_", "GIT_")

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

# SpawnHook deny-list: the dirty deny-list PLUS execve/execveat. A spawn
# decision only inspects the resolved spawn (argv + env NAMES) and returns a
# verdict -- it never shells out, so exec joins the network/escape primitives a
# spawn hook has no business reaching for.
_SPAWN_DENY_SYSCALLS = ("execve", "execveat", *_DIRTY_DENY_SYSCALLS)


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


# ---------------------------------------------------------------------------
# Parent-side launcher.
# ---------------------------------------------------------------------------


class SpawnHookInterrupted(Exception):
    """Raised by ``_run_child_process`` when ``should_stop()`` fires on a wake
    during a sandboxed spawn hook -- distinct from a plugin failure so the seam
    returns ``None`` WITHOUT emitting ``hook_failed`` (a stop is not the plugin's
    fault). Never raised on the dirty round path, which passes no ``wake_fd``."""


def run_hook_sandboxed(
    hook_kind: Literal["spawn_hook", "dirty_handler"],
    module_path: str,
    attr_path: str,
    hook_name: str,
    ctx: hooks.HookContext,
    *,
    log_dir: Path,
    dirty_files: list[str] | None = None,
    view: hooks.SpawnView | None = None,
    timeout_s: float = _TRAMPOLINE_TIMEOUT_S,
) -> SpawnDecision | DirtyOutcome | None:
    """Launch the confinement child for one third-party hook call and return its
    validated outcome. ``module_path``/``attr_path`` locate the plugin the child
    re-imports; they are threaded per-handler from the DISCOVERED entry point at
    registration (``hooks._DIRTY_HANDLER_MODULE`` / ``_SPAWN_HOOK_MODULE``), NOT
    derived from the collidable manifest name, so an entry-point name that differs
    from ``manifest.name`` still resolves. Secrets never cross the wire: only
    context field NAMES / plain values that ``_ctx_to_wire`` chooses are sent, and
    a ``SpawnView`` is reduced to its argv + env NAMES (``env_names``) -- never a
    value. A child killed by signal (seccomp KILL_PROCESS) emits
    ``plugin_sandbox_kill`` and raises; a non-zero exit or a timeout raises too --
    the caller (``dispatch_dirty`` / the spawn seam) isolates any raise as
    ``hook_failed``. Both child streams are redact-capped before any byte reaches
    an event."""
    payload = {
        "schema": "plugin_sandbox_input/1",
        "ctx": _ctx_to_wire(ctx),
        "hook_kind": hook_kind,
        "spawn_view": None
        if view is None
        else {"argv": list(view.argv), "env_names": list(view.env)},
        "dirty_files": dirty_files,
    }
    returncode, stdout, stderr = _run_child_process(
        [
            sys.executable,
            "-m",
            "agent_runner._plugin_sandbox",
            hook_kind,
            module_path,
            attr_path,
            hook_name,
        ],
        json.dumps(payload).encode("utf-8"),
        timeout_s,
    )
    if returncode < 0:
        from agent_runner.api import emit_plugin_sandbox_kill

        emit_plugin_sandbox_kill(log_dir, hook=hook_name, signal=-returncode)
        raise RuntimeError(f"trampoline for {hook_name} killed by signal {-returncode}")
    if returncode != 0:
        detail = hooks._cap_redacted(stderr.decode("utf-8", "replace"), _STDERR_CAP)
        raise RuntimeError(f"trampoline for {hook_name} exited {returncode}: {detail}")
    if hook_kind == "spawn_hook":
        return _parse_spawn_stdout(stdout)
    return _parse_dirty_stdout(stdout)


def _child_env() -> dict[str, str]:
    """Minimal default-DENY environment for the confined child (see the
    ``_ENV_ALLOW_*`` note). Only allowlisted vars are copied; every parent secret
    is left behind. ``PYTHONDONTWRITEBYTECODE`` is forced so the read-only import
    roots never see a .pyc write. ``PYTHONSAFEPATH`` is forced so ``python -m``
    does NOT prepend the cwd to ``sys.path``: ``_readable_roots`` grants Landlock
    read on every ``sys.path`` entry, so a cwd on the path would widen the child's
    read profile to the whole working directory (and, run ad-hoc from ``$HOME``/
    ``/``, to ``/proc/<ppid>/environ`` -- the parent's real secrets). Requires
    Python >= 3.11 (this project's floor)."""
    import os

    env = {
        name: value
        for name, value in os.environ.items()
        if name in _ENV_ALLOW_EXACT or name.startswith(_ENV_ALLOW_PREFIX)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONSAFEPATH"] = "1"
    return env


def _drain_capped(stream, cap: int, out: list[bytes]) -> None:
    """Read up to ``cap`` bytes, then drain+discard the rest so the child can never
    block on a full pipe (nor make us buffer more than the cap)."""
    data = bytearray()
    try:
        while len(data) < cap:
            chunk = stream.read(min(65536, cap - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        while stream.read(65536):
            pass
    finally:
        out.append(bytes(data))


def _run_child_process(
    argv: list[str],
    stdin_bytes: bytes,
    timeout_s: float,
    *,
    wake_fd: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[int, bytes, bytes]:
    """Spawn the confined child with the minimal env and read BOUNDED stdout/stderr
    concurrently (a thread per stream), so a plugin printing without limit within
    the wall-timeout cannot exhaust supervisor memory. Concurrent draining also
    avoids a full-pipe deadlock.

    The exit wait is the fd-driven ``_procwait.wait_exit`` (not a sleep-poll):
    ``"exited"`` reaps and returns; ``"timeout"`` kills and raises
    ``TimeoutExpired`` (the caller isolates it as ``hook_failed``); ``"woken"``
    (only when ``wake_fd`` is set) drains the advisory byte and, if
    ``should_stop()`` is now true, kills the child and raises
    ``SpawnHookInterrupted`` -- otherwise a stray ring re-enters the wait on the
    SAME ABSOLUTE deadline so it never shortens the hook's budget. The whole wait
    is wrapped so the confined child NEVER outlives its parent: any BaseException
    (a round ``KeyboardInterrupt``, a timeout, an interrupt) kills and reaps the
    child before propagating."""
    import threading

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
    )
    out_box: list[bytes] = []
    err_box: list[bytes] = []
    t_out = threading.Thread(target=_drain_capped, args=(proc.stdout, _MAX_WIRE_BYTES + 1, out_box))
    t_err = threading.Thread(target=_drain_capped, args=(proc.stderr, _MAX_STDERR_BYTES, err_box))
    t_out.start()
    t_err.start()
    try:
        try:
            proc.stdin.write(stdin_bytes)
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()
        deadline = SYSTEM_CLOCK.monotonic() + timeout_s
        while True:
            outcome = wait_exit(proc, deadline=deadline, wake_fd=wake_fd)
            if outcome == "exited":
                proc.wait()
                break
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(proc.args, timeout_s)
            _notify.drain(wake_fd)
            if should_stop is not None and should_stop():
                raise SpawnHookInterrupted(f"spawn hook interrupted for {argv[0]!r}")
    except BaseException:
        proc.kill()
        proc.wait()
        t_out.join()
        t_err.join()
        raise
    t_out.join()
    t_err.join()
    return proc.returncode, out_box[0], err_box[0]


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


def _parse_spawn_stdout(raw: bytes) -> SpawnDecision:
    """Validate + build the child's spawn verdict. The closed vocabulary is
    enforced here (unknown keys rejected, action in-vocabulary, defer_s a
    non-negative int), and ``reason`` is redact-capped before it can reach an
    event -- so a hostile child cannot smuggle a secret (or an argv splice)
    through this line."""
    if len(raw) > _MAX_WIRE_BYTES:
        raise ValueError("trampoline stdout exceeded wire cap")
    obj = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(obj, dict)
        or set(obj) - {"schema", "action", "defer_s", "reason"}
        or obj.get("schema") != "spawn_decision/1"
    ):
        shape = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise ValueError(f"invalid spawn_decision wire: {shape}")
    action = obj["action"]
    if action not in ("proceed", "defer", "skip"):
        raise ValueError(f"invalid spawn_decision action: {action!r}")
    defer_s = obj.get("defer_s", 0)
    if not isinstance(defer_s, int) or isinstance(defer_s, bool) or defer_s < 0:
        raise ValueError(f"invalid spawn_decision defer_s: {defer_s!r}")
    reason = obj.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError(f"invalid spawn_decision reason: {reason!r}")
    return SpawnDecision(
        action=action, defer_s=defer_s, reason=hooks._cap_redacted(reason, _REF_CAP)
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


def _apply_landlock_spawn(work_dir: Path, log_dir: Path) -> None:
    """SpawnHook Landlock profile: read the import roots, work_dir and log_dir;
    grant NO write and NO execute rule, and -- by NOT granting any net rule on a
    net-capable kernel -- deny the network. A spawn decision only inspects the
    resolved spawn; it never writes, execs, or talks to the network (the parent
    seam, not the child, records the decision event). Best-effort (seccomp is the
    primary denier): a kernel without Landlock, or without the sandbox extra,
    degrades silently here and leaves seccomp to enforce."""
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
        lock.allow_read(str(work_dir), str(log_dir))
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


def _preimport_seccomp() -> None:
    """Import the seccomp binding BEFORE any Landlock fs restriction is applied.

    ``pyseccomp``'s module init resolves libc via ``ctypes.util.find_library``,
    which probes the filesystem (``gettempdir()``/tempfile, and gcc/ld as a
    fallback). Once ``_apply_landlock_*`` restricts the fs to the profile's
    read-roots, that probe raises ``FileNotFoundError`` and the child dies before
    it can install the seccomp filter. Importing here -- while the fs is still
    open -- runs that init once and caches it in ``sys.modules``, so the later
    ``import pyseccomp`` inside ``_apply_seccomp_denylist`` (post-Landlock) is a
    no-op cache hit. No-op off-Linux (no binding, no seccomp)."""
    if sys.platform != "linux":
        return
    import pyseccomp  # noqa: F401 -- side-effecting import: run find_library init unrestricted


def _default_restrict_dirty(ctx: hooks.HookContext) -> None:
    import shutil

    git_bin = shutil.which("git")
    _preimport_seccomp()  # before Landlock: pyseccomp's find_library init needs the fs
    _apply_landlock_dirty(ctx.work_dir, ctx.log_dir, git_bin)
    _apply_seccomp_denylist(_DIRTY_DENY_SYSCALLS)


def _default_restrict_spawn(ctx: hooks.HookContext) -> None:
    _preimport_seccomp()  # before Landlock: pyseccomp's find_library init needs the fs
    _apply_landlock_spawn(ctx.work_dir, ctx.log_dir)
    _apply_seccomp_denylist(_SPAWN_DENY_SYSCALLS)


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

    target = resolve_entry_target(module_path, attr_path)
    handler = next(h for h in target.dirty_handlers if h.name == hook_name)
    outcome = handler.handle_dirty(ctx, dirty_files)
    if outcome is None:
        return {"schema": "dirty_outcome/1", "kind": None, "ref": None}
    return {"schema": "dirty_outcome/1", "kind": outcome.kind, "ref": outcome.ref}


def _run_spawn_child(
    module_path: str,
    attr_path: str,
    hook_name: str,
    ctx: hooks.HookContext,
    view: hooks.SpawnView,
    *,
    restrict,
) -> dict:
    """Self-restrict, THEN import the plugin and run its spawn hook -- same
    load-bearing order as ``_run_dirty_child``: the plugin's code executes for
    the FIRST time already confined. A ``None`` verdict serializes as ``proceed``
    so the closed vocabulary is the only shape crossing the wire."""
    restrict(ctx)

    target = resolve_entry_target(module_path, attr_path)
    hook = next(h for h in target.spawn_hooks if h.name == hook_name)
    decision = hook.before_spawn(ctx, view)
    if decision is None:
        return {"schema": "spawn_decision/1", "action": "proceed", "defer_s": 0, "reason": ""}
    return {
        "schema": "spawn_decision/1",
        "action": decision.action,
        "defer_s": decision.defer_s,
        "reason": decision.reason,
    }


def _main(argv: list[str]) -> int:
    hook_kind, module_path, attr_path, hook_name = argv[1], argv[2], argv[3], argv[4]
    if hook_kind not in ("dirty_handler", "spawn_hook"):
        raise SystemExit(f"plugin sandbox: unsupported hook_kind {hook_kind!r}")
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    ctx = _ctx_from_wire(data["ctx"])

    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # a plugin's own print() must never corrupt the JSON wire
    try:
        if hook_kind == "spawn_hook":
            sv = data["spawn_view"]
            # Reconstruct names-only: every env value is forced to "" here in the
            # child too, so even a child that ignored the parent's names-only wire
            # cannot read a secret VALUE from the view.
            view = hooks.SpawnView(
                argv=tuple(sv["argv"]),
                env=dict.fromkeys(sv["env_names"], ""),
            )
            result = _run_spawn_child(
                module_path,
                attr_path,
                hook_name,
                ctx,
                view,
                restrict=_default_restrict_spawn,
            )
        else:
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
