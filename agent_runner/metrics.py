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


def _read_psi(path: Path = _PSI_MEMORY_PATH) -> tuple[float, float] | None:
    """Parse ``/proc/pressure/memory``'s ``some``/``full`` ``avg10`` fields.

    Returns ``None`` when the file is absent (non-Linux, or a kernel built
    without ``CONFIG_PSI``) or unreadable (``psi=0`` boot param) — the
    caller (``host_health``) degrades gracefully down the signal ladder.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    some_avg10: float | None = None
    full_avg10: float | None = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
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
    return some_avg10, full_avg10 if full_avg10 is not None else 0.0


def sample() -> dict[str, Any]:
    """Lean, non-blocking read of the cache-poor-valid pressure signals.

    Deliberately NOT ``collect()`` (which shells out to ``pgrep`` for
    ``agent_process_count``, see ``_count_agent_processes`` below) — this is
    psutil counters + one optional ``/proc`` file read, safe to call every
    ~10s in a hot loop (e.g. a serve-loop mid-round check). ``host_health``
    is the pure interpreter of what this returns; this function only samples.

    ``swap_sout`` is cumulative (bytes swapped out since boot) — callers
    wanting a rate/delta diff two samples themselves.
    """
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    psi = _read_psi()
    return {
        "mem_available_mb": vm.available // (1024 * 1024),
        "mem_free_mb": vm.free // (1024 * 1024),
        "swap_sout": swap.sout,
        "psi_some_avg10": psi[0] if psi is not None else None,
        "psi_full_avg10": psi[1] if psi is not None else None,
    }


def collect(disk_path: Path, *, agent_binary: str | None = None) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(disk_path))
    out: dict[str, Any] = {
        "mem_total_mb": vm.total // (1024 * 1024),
        "mem_used_pct": round(vm.percent, 1),
        **sample(),
        "disk_total_gb": round(du.total / (1024**3), 1),
        "disk_free_gb": round(du.free / (1024**3), 1),
        "disk_used_pct": round(du.percent, 1),
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
    (``cli/_serve_round.py``'s ``_probe_and_emit_cgroup_defer``): a finite
    ``memory.max`` at or above this can never trigger cgroup-OOM before the
    HOST itself runs out of memory (e.g. a stale/copy-pasted
    ``MemoryMax=1G`` on a 462MB host), so deferring the host-wide floor in
    that shape would leave nothing armed to prevent coma. One-shot psutil
    read, no caching -- callers that need it once at startup (same as
    :func:`cgroup_memory_limits`) call it once."""
    return psutil.virtual_memory().total


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


def _read_finite_cgroup_limit(path: Path) -> int | None:
    """Read a cgroup ``memory.*`` limit file. ``"max"``, a missing file, or
    unparseable content all mean unlimited (``None``) -- that ancestor then
    contributes no finite candidate to the min-across-ancestors below."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text == "max" or not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _min_ancestor_limit(root: Path, ancestors: list[str], filename: str) -> int | None:
    """The MIN FINITE value of ``filename`` (a cgroup ``memory.*`` limit,
    e.g. ``"memory.max"``) across ``ancestors`` (nearest first, as returned
    by :func:`_cgroup_ancestors`) under ``root``. Each ancestor's file is
    read via :func:`_read_finite_cgroup_limit`, where ``"max"`` or a missing
    file contributes no candidate. ``None`` when no ancestor has a finite
    value -- unlimited end to end."""
    candidates = [
        limit
        for ancestor in ancestors
        if (limit := _read_finite_cgroup_limit(root / ancestor.lstrip("/") / filename)) is not None
    ]
    return min(candidates) if candidates else None


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

    ``root`` and ``proc_self_cgroup`` default to the real paths and are
    injectable so tests can point at a fake ``/sys/fs/cgroup`` tree;
    ``self_cgroup`` lets a caller supply the resolved cgroup path directly,
    skipping the ``/proc/self/cgroup`` read.

    One-shot pure file I/O (a handful of small reads), no clock -- serve
    probes once at startup and caches the result for the process's life.
    """
    if not (root / "cgroup.controllers").exists():
        return {"memory_max": None, "memory_swap_max": None, "cgroup_path": None}
    cgroup_path = self_cgroup if self_cgroup is not None else _self_cgroup_path(proc_self_cgroup)
    if cgroup_path is None:
        return {"memory_max": None, "memory_swap_max": None, "cgroup_path": None}

    ancestors = _cgroup_ancestors(cgroup_path)
    return {
        "memory_max": _min_ancestor_limit(root, ancestors, "memory.max"),
        "memory_swap_max": _min_ancestor_limit(root, ancestors, "memory.swap.max"),
        "cgroup_path": cgroup_path,
    }


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
