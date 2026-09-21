"""Compact round_start config digest — a label, never a kill input.

Hashes listed prompt-file paths **and bytes**, check cmdlines, [vcs],
prompt delivery, and agent env. Omits host-health terminate inputs and
give-up codes. Snapshot extras are paths/names only — no prompt text, no
env values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_runner.config.models import Config


def _resolve_prompt_path(cfg: Config, path: Path) -> Path:
    return path if path.is_absolute() else cfg.runtime.work_dir / path


def _file_sha256(path: Path) -> str | None:
    import hashlib  # function-scoped: hashlib is forbidden on serve startup

    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def digest_payload(cfg: Config, phase: str | None) -> dict[str, Any]:
    profile = cfg.profile_for(phase)
    if profile.prompt_files is not None:
        files = profile.prompt_files
    elif cfg.prompt.files:
        files = list(cfg.prompt.files)
    elif cfg.prompt.file is not None:
        files = [cfg.prompt.file]
    else:
        files = []
    resolved = [_resolve_prompt_path(cfg, p) for p in files]
    checks: list[dict[str, Any]] = []
    if cfg.goal is not None:
        checks = [{"name": c.name, "cmd": list(c.cmd)} for c in cfg.goal.checks]
    return {
        "prompt_files": [p.as_posix() for p in files],
        "prompt_file_sha256": [_file_sha256(p) for p in resolved],
        "checks": checks,
        "vcs_dirty_action": cfg.vcs.dirty_action,
        "prompt_delivery": profile.agent.prompt_delivery,
        "agent_env": sorted(profile.agent.env.items()),
    }


def _digest_hex(payload: dict[str, Any]) -> str:
    import hashlib  # function-scoped: hashlib is forbidden on serve startup

    blob = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def config_digest(cfg: Config, phase: str | None) -> str:
    return _digest_hex(digest_payload(cfg, phase))


def snapshot_fields(cfg: Config, phase: str | None) -> dict[str, Any]:
    return _snapshot(digest_payload(cfg, phase))


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_prompt_files": payload["prompt_files"],
        "config_check_names": [c["name"] for c in payload["checks"]],
        "config_vcs_dirty_action": payload["vcs_dirty_action"],
        "config_prompt_delivery": payload["prompt_delivery"],
    }


def last_round_start_digest(log_dir: Path) -> str | None:
    """Most recent ``config_digest`` on a ``round_start`` event, or None.

    Fail-open: a missing or unreadable JSONL is treated as no prior digest.
    """
    from agent_runner.events import ROUND_START, iter_event_dicts

    last: str | None = None
    try:
        paths = sorted(log_dir.glob("events-*.jsonl"))
    except OSError:
        return None
    for path in paths:
        try:
            for ev in iter_event_dicts(path):
                if ev.get("event") != ROUND_START:
                    continue
                digest = ev.get("config_digest")
                if isinstance(digest, str) and digest:
                    last = digest
        except OSError:
            continue
    return last


def round_start_fields(
    cfg: Config, phase: str | None, log_dir: Path, round_num: int
) -> dict[str, Any]:
    payload = digest_payload(cfg, phase)
    digest = _digest_hex(payload)
    changed = last_round_start_digest(log_dir) != digest
    fields: dict[str, Any] = {
        "round_num": round_num,
        "phase": phase,
        "config_digest": digest,
        "config_changed": changed,
    }
    if changed:
        fields.update(_snapshot(payload))
    return fields
