"""Cross-platform metrics — mem (system) + disk (log_dir partition) + load + cpu.

Same monthly UTC naming convention as events.jsonl.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import psutil

from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.events import now_iso_ms

_PSI_MEMORY_PATH = Path("/proc/pressure/memory")
_PSI_IO_PATH = Path("/proc/pressure/io")


def _read_psi(path: Path = _PSI_MEMORY_PATH) -> tuple[float, float, int | None] | None:
    """Parse a PSI file's ``some``/``full`` ``avg10`` and full-line ``total``.

    Returns ``(some_avg10, full_avg10, full_total)``. ``full_avg10`` is ``0.0``
    when the full line is missing; ``full_total`` is ``int(total=)`` from the
    full line, else ``None``. Returns ``None`` when the file is absent
    (non-Linux, or a kernel built without ``CONFIG_PSI``) or unreadable
    (``psi=0`` boot param) — the caller (``host_health``) degrades
    gracefully down the signal ladder.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    some_avg10: float | None = None
    full_avg10: float | None = None
    full_total: int | None = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        if parts[0] == "full":
            total_raw = fields.get("total")
            if total_raw is not None:
                try:
                    full_total = int(total_raw)
                except ValueError:
                    pass
        raw = fields.get("avg10")
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if parts[0] == "some":
            some_avg10 = value
        elif parts[0] == "full":
            full_avg10 = value
    if some_avg10 is None:
        return None
    return some_avg10, full_avg10 if full_avg10 is not None else 0.0, full_total


def sample() -> dict[str, Any]:
    """Lean, non-blocking read of the cache-poor-valid pressure signals.

    Deliberately NOT ``collect()`` (which shells out to ``pgrep`` for
    ``agent_process_count``, see ``_count_agent_processes`` below) — this is
    psutil counters + optional ``/proc/pressure/{memory,io}`` reads, safe to
    call every ~10s in a hot loop (e.g. a serve-loop mid-round check).
    ``host_health`` is the pure interpreter of what this returns; this
    function only samples. IO-PSI and mem-PSI ``total`` are corroborating
    fields the ladder ignores.

    ``swap_sout`` is cumulative (bytes swapped out since boot) — callers
    wanting a rate/delta diff two samples themselves.
    """
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    mem = _read_psi()
    io = _read_psi(_PSI_IO_PATH)
    return {
        "mem_available_mb": vm.available // (1024 * 1024),
        "mem_free_mb": vm.free // (1024 * 1024),
        "swap_sout": swap.sout,
        "psi_some_avg10": mem[0] if mem is not None else None,
        "psi_full_avg10": mem[1] if mem is not None else None,
        "psi_full_total": mem[2] if mem is not None else None,
        "io_psi_some_avg10": io[0] if io is not None else None,
        "io_psi_full_avg10": io[1] if io is not None else None,
    }


def collect(disk_path: Path, *, agent_binary: str | None = None) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(disk_path))
    st = os.statvfs(str(disk_path))
    # f_files==0 means the filesystem does not track inode counts at all (some
    # network/overlay filesystems) -- None signals "no signal", not "0% used".
    inode_used_pct = round(100 * (1 - st.f_ffree / st.f_files), 1) if st.f_files else None
    out: dict[str, Any] = {
        "mem_total_mb": vm.total // (1024 * 1024),
        "mem_used_pct": round(vm.percent, 1),
        **sample(),
        "disk_total_gb": round(du.total / (1024**3), 1),
        "disk_free_gb": round(du.free / (1024**3), 1),
        "disk_used_pct": round(du.percent, 1),
        "inode_used_pct": inode_used_pct,
    }
    try:
        load = os.getloadavg()
        out["load_1m"] = round(load[0], 2)
        out["load_5m"] = round(load[1], 2)
        out["load_15m"] = round(load[2], 2)
    except (AttributeError, OSError):
        pass
    try:
        out["cpu_pct"] = round(psutil.cpu_percent(interval=None), 1)
    except Exception:
        pass
    if agent_binary:
        out["agent_process_count"] = _count_agent_processes(agent_binary)
    return out


def _count_agent_processes(agent_binary: str) -> int:
    """Run `pgrep -xc <agent_binary>`; return count or 0 on error.

    Host-wide intentional — catches orphan agent processes not parented
    by us, which is the diagnostic value of this metric.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-xc", agent_binary],
            capture_output=True,
            text=True,
            timeout=2,
        )
        # pgrep -c returns exit 1 with output "0" when no matches; exit 0
        # with count otherwise. Both are valid; non-int output → 0.
        if result.returncode in (0, 1):
            return int(result.stdout.strip() or "0")
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, OSError):
        pass
    return 0


def mem_total_bytes() -> int:
    """Host total RAM in bytes (``psutil.virtual_memory().total``) -- the
    plausibility ceiling for the cgroup auto-defer decision
    (``cli/_serve_cgroup.py``'s ``_probe_and_emit_cgroup_defer``): a finite
    ``memory.max`` at or above this can never trigger cgroup-OOM before the
    HOST itself runs out of memory (e.g. a stale/copy-pasted
    ``MemoryMax=1G`` on a 462MB host), so deferring the host-wide floor in
    that shape would leave nothing armed to prevent coma. One-shot psutil
    read, no caching -- callers that need it once at startup (same as
    :func:`cgroup_memory_limits`) call it once."""
    return psutil.virtual_memory().total


def swap_total_bytes() -> int:
    """Host total swap in bytes (``psutil.swap_memory().total``) -- the
    plausibility ceiling for both the cgroup-defer swap-plausibility guard
    (a ``memory.swap.max`` far ABOVE this can't bind before host-wide swap
    exhaustion either, so it must not disarm the mid-round floor) and the
    startup swap-cap advisory (a ``memory.swap.max`` far BELOW this means the
    operator capped the cgroup's swap well under what the host actually has
    -- the floor may terminate a round the kernel would have contained on a
    wider cap). One-shot psutil read, no caching -- same shape as
    :func:`mem_total_bytes`."""
    return psutil.swap_memory().total


_CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")


def _self_cgroup_path(proc_self_cgroup: Path) -> str | None:
    """Parse the cgroup v2 unified-hierarchy line (``0::<path>``) out of
    ``/proc/self/cgroup``. Returns ``None`` when the file is unreadable or
    carries no ``0::`` line (a pure cgroup-v1 host has none)."""
    try:
        text = proc_self_cgroup.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[len("0::") :]
    return None


def _cgroup_ancestors(cgroup_path: str) -> list[str]:
    """This cgroup and every ancestor up to (and including) the root
    ``"/"``, nearest first -- a bounding systemd slice's ``memory.max``
    constrains every scope nested beneath it, so the tightest ancestor (not
    necessarily the leaf) must be considered."""
    parts = [p for p in cgroup_path.split("/") if p]
    ancestors = ["/" + "/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    ancestors.append("/")
    return ancestors


def _parse_cgroup_token(text: str) -> int | None:
    """Collapse a cgroup limit token to a finite byte count. ``"max"``, empty,
    or unparseable content all mean unlimited (``None``). Shared by
    :func:`_read_finite_cgroup_limit` (which reads the token off a path) and by
    the soft-brake's monotone clamp (which already holds the raw token as its
    restore stash)."""
    if text == "max" or not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_finite_cgroup_limit(path: Path) -> int | None:
    """Read a cgroup ``memory.*`` limit file. ``"max"``, a missing file, or
    unparseable content all mean unlimited (``None``) -- that ancestor then
    contributes no finite candidate to the min-across-ancestors below."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return _parse_cgroup_token(text)


def _min_ancestor_candidate(
    root: Path, ancestors: list[str], filename: str
) -> tuple[int, str] | None:
    """The ``(MIN FINITE value, owning ancestor)`` pair for ``filename`` (a
    cgroup ``memory.*`` limit file) across ``ancestors`` (nearest first, as
    returned by :func:`_cgroup_ancestors`) under ``root``. Each ancestor's
    file is read via :func:`_read_finite_cgroup_limit`, where ``"max"`` or a
    missing file contributes no candidate. ``None`` when no ancestor has a
    finite value -- unlimited end to end. Shared resolution behind
    :func:`_min_ancestor_limit` (value only) and :func:`_bounding_ancestor_path`
    (path only, always for ``"memory.max"``) -- both need the SAME winning
    ancestor, not independently re-derived ones."""
    best: tuple[int, str] | None = None
    for ancestor in ancestors:
        limit = _read_finite_cgroup_limit(root / ancestor.lstrip("/") / filename)
        if limit is not None and (best is None or limit < best[0]):
            best = (limit, ancestor)
    return best


def _min_ancestor_limit(root: Path, ancestors: list[str], filename: str) -> int | None:
    """The MIN FINITE value of ``filename`` (a cgroup ``memory.*`` limit,
    e.g. ``"memory.max"``) across ``ancestors`` (nearest first, as returned
    by :func:`_cgroup_ancestors`) under ``root``. ``None`` when no ancestor
    has a finite value -- unlimited end to end."""
    candidate = _min_ancestor_candidate(root, ancestors, filename)
    return candidate[0] if candidate is not None else None


def _bounding_ancestor_path(root: Path, ancestors: list[str]) -> str | None:
    """The ancestor with the MIN FINITE ``memory.max`` -- the real budget's
    owner, and the SAME ancestor :func:`cgroup_memory_limits` reports as
    ``memory_max``. This is the path the per-round usage read
    (:func:`cgroup_memory_usage`) targets, so ``memory.current`` /
    ``memory.events`` are read at the SAME level the limit binds (a
    hierarchical cgroup counter at a looser descendant would undercount --
    it only sees its own subtree, not siblings sharing the bounding
    ancestor's budget). ``None`` when no ancestor has a finite ``memory.max``."""
    candidate = _min_ancestor_candidate(root, ancestors, "memory.max")
    return candidate[1] if candidate is not None else None


def _resolve_cgroup(
    root: Path, proc_self_cgroup: Path, self_cgroup: str | None
) -> tuple[str, list[str]] | None:
    """Shared cgroup-v2-availability + ancestor-resolution prologue for
    :func:`cgroup_memory_limits`, :func:`cgroup_memory_high`, and
    :func:`cgroup_memory_usage`: ``(cgroup_path, ancestors)``, or ``None``
    when cgroup v2 is unavailable (``root/cgroup.controllers`` missing) or
    ``self_cgroup``/``/proc/self/cgroup`` did not resolve to a path.

    ``self_cgroup`` lets a caller supply the resolved cgroup path directly,
    skipping the ``/proc/self/cgroup`` read (see each caller's docstring)."""
    if not (root / "cgroup.controllers").exists():
        return None
    cgroup_path = self_cgroup if self_cgroup is not None else _self_cgroup_path(proc_self_cgroup)
    if cgroup_path is None:
        return None
    return cgroup_path, _cgroup_ancestors(cgroup_path)


def _read_events_counters(path: Path) -> dict[str, int]:
    """Parse a cgroup v2 ``memory.events`` file (``key value`` lines per
    line) into the ``high``/``max``/``oom``/``oom_kill`` counters. These are
    ABSOLUTE, monotonically-increasing counters since cgroup creation --
    callers wanting a per-round signal must diff two reads (see
    :func:`cgroup_memory_usage`'s docstring)."""
    out: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("high", "max", "oom", "oom_kill"):
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def cgroup_memory_limits(
    *,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
) -> dict[str, int | str | None]:
    """Probe this process's cgroup v2 memory budget: the MIN FINITE
    ``memory.max`` and ``memory.swap.max`` across this cgroup and every
    ancestor up to ``root``, computed independently for each field. A
    bounding systemd slice's limit constrains every scope nested beneath
    it, so the tightest ancestor -- not necessarily the leaf -- is the real
    budget. ``None`` for either field means unlimited: every ancestor read
    ``"max"`` or the file was missing.

    Requires cgroup v2 (``root/cgroup.controllers`` present); a pure v1
    host, or any host missing that file, has no reliable fixed-path budget
    file to read, so this returns all-``None`` rather than guessing.

    ``cgroup_path`` is always the caller's OWN leaf; ``bounding_cgroup_path``
    is the ancestor (possibly the leaf itself) that actually owns the
    winning ``memory.max`` (see :func:`_bounding_ancestor_path`) -- callers
    that need to tell "my own limit" from "an inherited parent/container
    bound" compare the two.

    ``root`` and ``proc_self_cgroup`` default to the real paths and are
    injectable so tests can point at a fake ``/sys/fs/cgroup`` tree;
    ``self_cgroup`` lets a caller supply the resolved cgroup path directly,
    skipping the ``/proc/self/cgroup`` read.

    One-shot pure file I/O (a handful of small reads), no clock -- serve
    probes once at startup and caches the result for the process's life.
    """
    resolved = _resolve_cgroup(root, proc_self_cgroup, self_cgroup)
    if resolved is None:
        return {
            "memory_max": None,
            "memory_swap_max": None,
            "cgroup_path": None,
            "bounding_cgroup_path": None,
        }
    cgroup_path, ancestors = resolved
    # One ancestor walk for memory.max, shared for both its value and its
    # owning path (the "SAME winning ancestor" _min_ancestor_candidate's
    # docstring promises) -- memory.swap.max is a distinct file, its own walk.
    max_candidate = _min_ancestor_candidate(root, ancestors, "memory.max")
    return {
        "memory_max": max_candidate[0] if max_candidate else None,
        "memory_swap_max": _min_ancestor_limit(root, ancestors, "memory.swap.max"),
        "cgroup_path": cgroup_path,
        "bounding_cgroup_path": max_candidate[1] if max_candidate else None,
    }


def cgroup_memory_high(
    *,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
) -> int | None:
    """Probe this process's cgroup v2 ``memory.high`` (the soft throttle
    threshold, distinct from the hard ``memory.max`` kill ceiling): the MIN
    FINITE value across this cgroup and every ancestor up to ``root`` --
    same bounding-ancestor walk as :func:`cgroup_memory_limits` uses for
    ``memory.max``/``memory.swap.max`` (:func:`_min_ancestor_limit` over
    :func:`_min_ancestor_candidate`), just for a different filename.

    ``None`` means unset: every ancestor read the literal ``"max"``, the
    file was missing, cgroup v2 is unavailable, or the cgroup path could not
    be resolved -- deliberately collapsed to ``None`` rather than passing
    the raw ``"max"`` token through, so a caller can never mistake "unset"
    for a real ceiling. Kept separate from :func:`cgroup_memory_limits`'s
    return dict so that function's existing exact-dict-equality tests need
    no change for an unrelated field.

    Same params/defaults as :func:`cgroup_memory_limits` (injectable for
    tests); one-shot pure file I/O, no clock."""
    resolved = _resolve_cgroup(root, proc_self_cgroup, self_cgroup)
    if resolved is None:
        return None
    _cgroup_path, ancestors = resolved
    return _min_ancestor_limit(root, ancestors, "memory.high")


def cgroup_delegated(
    *,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
) -> bool | None:
    """Whether THIS process's own cgroup v2 leaf is delegated to it: uid-owned
    AND both cgroup.procs and memory.high are writable. Read-only -- tests
    writability via os.access(path, os.W_OK) against the existing directory;
    NEVER opens, creates, or writes either file. None when cgroup v2 is
    unavailable or the leaf can't be resolved (mirrors cgroup_path=None
    elsewhere in this module) -- distinct from False (cgroup v2 present,
    but NOT delegated to this process).

    Delegation (systemd's Delegate=yes) is the prerequisite a future release
    needs before it may safely write memory.high on a round-scoped nested
    cgroup -- this probe only ever answers the readiness question; it never
    itself writes anything.
    """
    resolved = _resolve_cgroup(root, proc_self_cgroup, self_cgroup)
    if resolved is None:
        return None
    cgroup_path, _ancestors = resolved
    leaf_dir = root / cgroup_path.lstrip("/")
    try:
        uid_owned = leaf_dir.stat().st_uid == os.getuid()
    except OSError:
        return None
    procs_writable = os.access(leaf_dir / "cgroup.procs", os.W_OK)
    high_writable = os.access(leaf_dir / "memory.high", os.W_OK)
    return uid_owned and procs_writable and high_writable


def cgroup_memory_usage(
    *,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
    bounding_cgroup: str | None = None,
) -> dict[str, Any]:
    """Per-round pressure read at the BOUNDING ancestor (the one whose
    ``memory.max`` is the tightest finite value -- the same one
    :func:`cgroup_memory_limits` picks for ``memory_max``, via
    :func:`_bounding_ancestor_path`). Returns ``memory_current``,
    ``memory_swap_current``, ``memory_events`` (dict), ``cgroup_path``; or
    ``{}`` when cgroup v2 is unavailable or no ancestor has a finite
    ``memory.max`` (nothing bounds this process, so there is no round-scoped
    budget to read pressure against).

    ``memory_events`` fields are ABSOLUTE counters since cgroup creation --
    callers wanting a per-round signal (e.g. ``round_cgroup_memory``) must
    diff two reads, never report these fields directly.

    ``bounding_cgroup`` lets a caller supply an ALREADY-RESOLVED bounding
    ancestor path (the ``cgroup_path`` a prior call in the SAME round
    already returned) directly, skipping both the ``/proc/self/cgroup`` read
    AND the per-ancestor ``memory.max`` walk that finds it. The bounding
    ancestor cannot change once a round has started, so a caller doing
    repeated per-round reads -- ``_spawn_round``'s mid-round ticks,
    ``_emit_round_cgroup_memory``'s round-end read -- resolves once (the
    round's first, un-cached read) and passes the result back in on every
    later read for that same round; a later round must NOT reuse it (the
    bounding cgroup can differ across serve restarts / config changes), so
    the cache lives only as long as the caller's own per-round state does.
    Takes priority over ``self_cgroup`` when both are given; ``self_cgroup``
    alone still only skips the ``/proc/self/cgroup`` read, not the ancestor
    walk -- ``bounding_cgroup`` is the one that skips it too.

    One-shot pure file I/O, no clock -- ``_spawn_round`` calls this once at
    round start (baseline, un-cached) and again on each existing ~10s
    mid-round tick (peak tracking, cached via ``bounding_cgroup``);
    ``post_round_verdicts`` calls it once more at the round boundary for the
    delta (also cached).

    Fail-open on ``OSError``: this runs inside ``_spawn_round``'s mid-round
    tick loop, which is itself inside a ``try / except BaseException:
    _terminate_round(proc); raise`` -- a non-ENOENT stat error (EACCES/EIO on
    a flaky sysfs) would otherwise terminate the round AND crash serve.
    ``Path.exists()`` only swallows a narrow ENOENT/ENOTDIR/EBADF/ELOOP set
    and re-raises everything else, so that call is the one this function
    cannot leave unguarded even though every helper it calls below already
    catches ``OSError`` on its own."""
    try:
        if bounding_cgroup is not None:
            bounding = bounding_cgroup
            base = root / bounding.lstrip("/")
            if not base.is_dir():
                # The cached ancestor from an earlier read in this round is
                # gone (renamed/removed mid-round). Without this check we'd
                # fall through to reading memory.current etc. below, which
                # return 0/{} for a MISSING path -- a truthy all-zero dict
                # that looks like "no pressure" instead of "can no longer
                # tell". Report the same "can no longer tell" `{}` the
                # un-cached path already returns when nothing bounds the
                # process, so callers (`_emit_round_cgroup_memory`) skip
                # instead of emitting a stale, misleading zero delta.
                return {}
        else:
            resolved = _resolve_cgroup(root, proc_self_cgroup, self_cgroup)
            if resolved is None:
                return {}
            _cgroup_path, ancestors = resolved
            bounding = _bounding_ancestor_path(root, ancestors)
            if bounding is None:
                return {}
            base = root / bounding.lstrip("/")
    except OSError:
        return {}
    return {
        "memory_current": _read_finite_cgroup_limit(base / "memory.current") or 0,
        "memory_swap_current": _read_finite_cgroup_limit(base / "memory.swap.current") or 0,
        "memory_events": _read_events_counters(base / "memory.events"),
        "cgroup_path": bounding,
    }


_MIN_MEMORY_HIGH_BYTES = 64 * 1024 * 1024  # never write memory.high below this
_MIN_BRAKE_CURRENT_BYTES = 128 * 1024 * 1024  # don't engage when the leaf is too small to matter


def _brake_high_value(memory_current: int, step_pct: int) -> int | None:
    """The soft-brake memory.high to write: ``current x (1 - step_pct/100)``,
    floored at :data:`_MIN_MEMORY_HIGH_BYTES`. ``None`` when the leaf's
    ``memory.current`` is below :data:`_MIN_BRAKE_CURRENT_BYTES` -- too small to
    be the host's problem, so the brake does not engage. Pure arithmetic, off
    the cold import graph."""
    if memory_current < _MIN_BRAKE_CURRENT_BYTES:
        return None
    return max(int(memory_current * (1 - step_pct / 100)), _MIN_MEMORY_HIGH_BYTES)


def _leaf_dir(root: Path, proc_self_cgroup: Path, self_cgroup: str | None) -> Path | None:
    """This process's OWN resolved cgroup v2 leaf directory (never an
    ancestor), or ``None`` when cgroup v2 is unavailable / the leaf can't be
    resolved. The soft-brake's write target -- shared by the read/stash and the
    write below so both address the identical path."""
    resolved = _resolve_cgroup(root, proc_self_cgroup, self_cgroup)
    if resolved is None:
        return None
    cgroup_path, _ancestors = resolved
    return root / cgroup_path.lstrip("/")


def _read_cgroup_raw(path: Path) -> str | None:
    """The raw stripped token of a cgroup file (``"max"`` or a decimal), or
    ``None`` on OSError. Distinct from :func:`_read_finite_cgroup_limit` (which
    collapses ``"max"`` to ``None``): the stash must preserve ``"max"`` so
    restore writes back exactly what systemd last set, never a blind finite value."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def engage_leaf_memory_high(
    *,
    step_pct: int,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
) -> dict[str, Any]:
    """Reversibly write the soft-brake ``memory.high`` on serve's OWN leaf
    cgroup -- NEVER an ancestor. Reads the leaf's ``memory.current`` (re-read
    live, never a cached loop value -- systemd re-applies ``MemoryHigh=`` on
    daemon-reload), computes :func:`_brake_high_value`, stashes the leaf's own
    prior ``memory.high`` raw token for restore, and writes via
    ``os.open(O_WRONLY|O_TRUNC)`` + ``os.write`` of a decimal byte count.

    Fail-OPEN. Returns ``{}`` (no engage: unresolvable leaf, ``memory.current``
    below the engage floor / unreadable, or the leaf's existing finite
    ``memory.high`` is already at/below the computed target -- the brake
    never raises a throttle), ``{"engaged": True, "previous", "written",
    "memory_current"}`` on a successful write, or ``{"engaged": False,
    "errno"}`` on an OSError at read or write (the caller emits
    ``memory_high_write_failed`` once and disarms for the round)."""
    leaf = _leaf_dir(root, proc_self_cgroup, self_cgroup)
    if leaf is None:
        return {}
    current = _read_finite_cgroup_limit(leaf / "memory.current")
    if current is None:
        return {}
    target = _brake_high_value(current, step_pct)
    if target is None:
        return {}
    previous = _read_cgroup_raw(leaf / "memory.high")
    if previous is None:
        return {}
    previous_int = _parse_cgroup_token(previous)
    if previous_int is not None and target >= previous_int:
        return {}  # a soft-brake is monotone: never raise an existing tighter high
    try:
        fd = os.open(str(leaf / "memory.high"), os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(fd, str(target).encode("ascii"))
        finally:
            os.close(fd)
    except OSError as e:
        return {"engaged": False, "errno": e.errno}
    return {"engaged": True, "previous": previous, "written": target, "memory_current": current}


def restore_leaf_memory_high(
    previous: str,
    *,
    root: Path = _CGROUP_ROOT,
    proc_self_cgroup: Path = _PROC_SELF_CGROUP,
    self_cgroup: str | None = None,
) -> bool:
    """Write the STASHED raw token (``"max"`` or a decimal) back to the leaf's
    own ``memory.high``. Fail-OPEN: returns ``False`` on an unresolvable leaf or
    OSError (the caller emits loudly and retries at the next round boundary +
    serve exit). Write target is ALWAYS the resolved leaf, never an ancestor."""
    leaf = _leaf_dir(root, proc_self_cgroup, self_cgroup)
    if leaf is None:
        return False
    try:
        fd = os.open(str(leaf / "memory.high"), os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(fd, str(previous).encode("ascii"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def log_metrics(
    log_dir: Path,
    *,
    event: str = "periodic",
    round_num: int | None = None,
    phase: str | None = None,
    agent_binary: str | None = None,
) -> None:
    """Append one metrics sample to metrics-YYYY-MM.jsonl (UTC).

    Caller must ensure ``log_dir`` exists. Disk-usage stats are sampled from
    ``log_dir``'s partition (callers that wanted a different mount can reach
    for psutil directly — single-mount is the only real-world case so far).
    """
    month = SYSTEM_CLOCK.now_utc().strftime("%Y-%m")
    path = log_dir / f"metrics-{month}.jsonl"
    payload: dict[str, Any] = {
        "ts": now_iso_ms(),
        "event": event,
        **collect(log_dir, agent_binary=agent_binary),
    }
    if round_num is not None:
        payload["round_num"] = round_num
    if phase is not None:
        payload["phase"] = phase
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
