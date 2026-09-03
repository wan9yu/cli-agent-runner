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
