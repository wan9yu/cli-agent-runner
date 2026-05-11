"""Shared CLI helpers — config loading + JSON formatting."""

from __future__ import annotations

import dataclasses
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from agent_runner.config import Config, load_config


def cfg_from_args(args) -> Config:
    return load_config(args.config)


def work_dir_from_args(args) -> Path:
    """Resolve the project work_dir from --config's parent, falling back to cwd."""
    cfg = getattr(args, "config", None)
    if cfg is None:
        return Path.cwd().resolve()
    return Path(cfg).resolve().parent


def emit(value: Any, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(_to_jsonable(value), indent=2, default=str))
    else:
        print(_pretty(value))


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(x) for x in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _pretty(value: Any) -> str:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        lines: list[str] = [type(value).__name__ + ":"]
        for f in dataclasses.fields(value):
            v = getattr(value, f.name)
            lines.append(f"  {f.name}: {_pretty_inline(v)}")
        return "\n".join(lines)
    return _pretty_inline(value)


def _pretty_inline(value: Any) -> str:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def fail(msg: str, *, code: int = 1) -> int:
    print(f"agent-runner: {msg}", file=sys.stderr)
    return code
