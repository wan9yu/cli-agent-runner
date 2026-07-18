"""Persistent JSON state — status / round-context / orphan-state, atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATUS_FILE = "status.json"
CONTEXT_FILE = "round-context.json"
ORPHAN_FILE = "orphan-state.json"


@dataclass(frozen=True)
class Status:
    round_num: int
    running: bool
    last_completed_at: str | None = None
    last_exit_code: int | None = None
    last_duration_s: float | None = None
    current_phase: str | None = None
    phase_index: int = 0


@dataclass(frozen=True)
class OrphanState:
    round_num: int
    files: list[str]
    stashed_ref: str | None
    stash_message: str | None
    timestamp: str
    phase: str | None = None


def atomic_write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    """Write JSON atomically: tmp file in same dir, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    """Read + parse JSON; return None on missing file or parse failure.

    Single TOCTOU-free read replaces three near-identical exists+read patterns.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_status(log_dir: Path, status: Status) -> None:
    payload = {k: v for k, v in asdict(status).items() if v is not None or isinstance(v, bool)}
    atomic_write_json(log_dir / STATUS_FILE, payload)


def read_status(log_dir: Path) -> Status | None:
    data = read_json(log_dir / STATUS_FILE)
    if data is None:
        return None
    try:
        return Status(**data)
    except TypeError:
        return None


def write_orphan_state(log_dir: Path, state: OrphanState) -> None:
    atomic_write_json(log_dir / ORPHAN_FILE, asdict(state))


def read_orphan_state(log_dir: Path) -> OrphanState | None:
    data = read_json(log_dir / ORPHAN_FILE)
    if data is None:
        return None
    try:
        return OrphanState(**data)
    except TypeError:
        return None


def clear_orphan_state(log_dir: Path) -> None:
    (log_dir / ORPHAN_FILE).unlink(missing_ok=True)
