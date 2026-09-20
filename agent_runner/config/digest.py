"""Compact round_start config digest — a label, never a kill input.

Hashes the Config-reload hot surfaces (prompt files, check cmdlines, [vcs],
prompt delivery, agent env). Omits host-health terminate inputs and give-up
codes. Snapshot extras omit env values so secrets never land in JSONL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_runner.config.models import Config


def _prompt_files(cfg: Config, phase: str | None) -> list[str]:
    profile = cfg.profile_for(phase)
    if profile.prompt_files is not None:
        files = profile.prompt_files
    elif cfg.prompt.files:
        files = list(cfg.prompt.files)
    elif cfg.prompt.file is not None:
        files = [cfg.prompt.file]
    else:
        files = []
    return [p.as_posix() for p in files]


def digest_payload(cfg: Config, phase: str | None) -> dict[str, Any]:
    profile = cfg.profile_for(phase)
    checks: list[dict[str, Any]] = []
    if cfg.goal is not None:
        checks = [{"name": c.name, "cmd": list(c.cmd)} for c in cfg.goal.checks]
    return {
        "prompt_files": _prompt_files(cfg, phase),
        "checks": checks,
        "vcs_dirty_action": cfg.vcs.dirty_action,
        "prompt_delivery": profile.agent.prompt_delivery,
        "agent_env": sorted(profile.agent.env.items()),
    }


def config_digest(cfg: Config, phase: str | None) -> str:
    import hashlib  # function-scoped: hashlib is forbidden on serve startup

    blob = json.dumps(
        digest_payload(cfg, phase),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def snapshot_fields(cfg: Config, phase: str | None) -> dict[str, Any]:
    payload = digest_payload(cfg, phase)
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
    last: str | None = None
    try:
        paths = sorted(log_dir.glob("events-*.jsonl"))
    except OSError:
        return None
    from agent_runner.events import iter_event_dicts

    for path in paths:
        try:
            for ev in iter_event_dicts(path):
                if ev.get("event") != "round_start":
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
    digest = config_digest(cfg, phase)
    prev = last_round_start_digest(log_dir)
    fields: dict[str, Any] = {
        "round_num": round_num,
        "phase": phase,
        "config_digest": digest,
        "config_changed": prev != digest,
    }
    if fields["config_changed"]:
        fields.update(snapshot_fields(cfg, phase))
    return fields
