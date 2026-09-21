"""config_digest hashes Config-reload surfaces, not host-health floors."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    MonitorConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
    config_digest,
    snapshot_fields,
)
from agent_runner.config.models import GoalConfig, _GoalCheckConfig


def _cfg(tmp_path: Path, **agent_kw) -> Config:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x" * 500)
    return Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=[], **agent_kw),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(),
    )


def test_config_digest_should_change_when_prompt_file_changes(tmp_path: Path) -> None:
    a = _cfg(tmp_path)
    other = tmp_path / "other.md"
    other.write_text("y" * 500)
    b = dataclasses.replace(a, prompt=PromptConfig(file=other))
    assert config_digest(a, None) != config_digest(b, None)


def test_config_digest_should_change_when_prompt_contents_change_on_same_path(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    before = config_digest(cfg, None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("y" * 500)
    after = config_digest(cfg, None)
    assert before != after
    snap = snapshot_fields(cfg, None)
    assert "yyyy" not in str(snap)
    assert snap["config_prompt_files"] == [prompt.as_posix()]


def test_config_digest_should_ignore_host_health_floors(tmp_path: Path) -> None:
    a = _cfg(tmp_path)
    mem = dataclasses.replace(
        a.monitor.host_health.memory, free_low_mb=a.monitor.host_health.memory.free_low_mb + 8
    )
    hh = dataclasses.replace(a.monitor.host_health, memory=mem)
    b = dataclasses.replace(a, monitor=MonitorConfig(host_health=hh))
    assert config_digest(a, None) == config_digest(b, None)


def test_snapshot_fields_should_omit_agent_env_values(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, env={"TOKEN": "secret"})
    snap = snapshot_fields(cfg, None)
    blob = str(snap)
    assert "secret" not in blob
    assert "TOKEN" not in blob
    assert "config_prompt_delivery" in snap


def test_config_digest_should_change_when_check_cmd_changes(tmp_path: Path) -> None:
    a = _cfg(tmp_path)
    ledger = tmp_path / "logs" / "lessons.md"
    a = dataclasses.replace(a, goal=GoalConfig(ledger=str(ledger), checks=()))
    b = dataclasses.replace(
        a,
        goal=GoalConfig(
            ledger=str(ledger),
            checks=(_GoalCheckConfig(name="tests", cmd=["pytest", "-q"]),),
        ),
    )
    assert config_digest(a, None) != config_digest(b, None)
