"""Cross-platform metrics — mem (system) + disk (log_dir partition) + load + cpu.

Same monthly UTC naming convention as events.jsonl.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from agent_runner.events import now_iso_ms


def collect(disk_path: Path, *, agent_binary: str | None = None) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(disk_path))
    out: dict[str, Any] = {
        "mem_total_mb": vm.total // (1024 * 1024),
        "mem_available_mb": vm.available // (1024 * 1024),
        "mem_used_pct": round(vm.percent, 1),
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
    month = datetime.now(UTC).strftime("%Y-%m")
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
