from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.runner import (
    LockHeldError,
    _acquire_lock_or_raise,
    _round_timeout_for,
    _scan_round_log_for_network_blip,
    run_one_round,
)


def _make_config(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    phases: list[str] | None = None,
) -> Config:
    # log_dir, prompt and the agent script live OUTSIDE the work_dir so that
    # any stash of the agent's dirty tree (git stash -u removes untracked
    # files) does not erase log_dir state, the prompt, or the script across
    # rounds. The shared fixture happens to drop fake-agent.sh into the same
    # tmp_path as the git repo; copy it into the sandbox to detach it.
    # Sandbox sibling of work_dir, scoped to this test's tmp_path leaf so
    # parallel/sequential tests do not bleed status.json into each other.
    sandbox = tmp_git_repo.parent / f"runner-sandbox-{tmp_git_repo.name}"
    sandbox.mkdir(exist_ok=True)
    log_dir = sandbox / "logs"
    prompt = sandbox / "prompt.md"
    prompt.write_text("Test prompt body. " * 50)
    script_copy = sandbox / fake_agent_script.name
    shutil.copy2(fake_agent_script, script_copy)
    script_copy.chmod(0o755)
    return Config(
        agent=AgentConfig(command=[str(script_copy)], prompt_arg_template=[]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir, round_timeout_s=10),
        prompt=PromptConfig(file=prompt, inject_context=True),
        vcs=VcsConfig(),
        phases=phases,
    )


def test_given_first_round_when_run_then_status_round_num_is_one(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    result = run_one_round(cfg)
    assert result.exit_code == 0
    status = json.loads((cfg.runtime.log_dir / "status.json").read_text())
    assert status["round_num"] == 1
    assert status["last_exit_code"] == 0


def test_given_three_runs_when_invoked_sequentially_then_round_num_increments(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    for expected in (1, 2, 3):
        run_one_round(cfg)
        status = json.loads((cfg.runtime.log_dir / "status.json").read_text())
        assert status["round_num"] == expected


def test_given_phases_configured_when_run_then_phase_in_round_context(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script, phases=["a", "b"])
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert ctx["phase"] == "a"
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert ctx["phase"] == "b"


def test_given_phases_unconfigured_when_run_then_phase_field_absent(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script, phases=None)
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert "phase" not in ctx


def test_given_corrupt_status_when_run_then_recovers_and_emits_event(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.runtime.log_dir / "status.json").write_text("{ corrupt")
    run_one_round(cfg)
    events_files = list(cfg.runtime.log_dir.glob("events-*.jsonl"))
    assert events_files, "events.jsonl should have been written"
    events = [json.loads(line) for line in events_files[0].read_text().splitlines()]
    assert any(e["event"] == "status_recovered" for e in events)


def test_given_lock_held_when_acquire_then_raises_lockheld(tmp_path: Path) -> None:
    lock_path = tmp_path / "agent-runner.lock"
    fd = _acquire_lock_or_raise(lock_path)
    try:
        with pytest.raises(LockHeldError):
            _acquire_lock_or_raise(lock_path)
    finally:
        os.close(fd)


def test_given_no_existing_lock_when_acquire_then_returns_fd(tmp_path: Path) -> None:
    fd = _acquire_lock_or_raise(tmp_path / "agent-runner.lock")
    try:
        assert isinstance(fd, int)
    finally:
        os.close(fd)


def test_given_smoke_check_fail_when_run_one_round_then_exits_without_spawning_agent(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    cfg.prompt.file.unlink()  # break prompt — startup smoke fails
    with pytest.raises(SystemExit) as exc:
        run_one_round(cfg)
    assert exc.value.code == 1


def _unit_cfg(
    tmp_path: Path,
    *,
    round_timeout_s: int = 1800,
    round_timeout_per_phase: dict[str, int] | None = None,
    phases: list[str] | None = None,
) -> Config:
    """Minimal Config for unit-level helper tests (no sandbox/script setup)."""
    return Config(
        agent=AgentConfig(command=["fake-agent"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
            round_timeout_s=round_timeout_s,
            round_timeout_per_phase=round_timeout_per_phase or {},
        ),
        prompt=PromptConfig(file=tmp_path / "p.md", inject_context=True),
        vcs=VcsConfig(),
        phases=phases,
    )


def test_given_phase_in_per_phase_dict_when_lookup_then_returns_phase_value(
    tmp_path: Path,
) -> None:
    """_round_timeout_for returns the phase-specific value when present."""
    cfg = _unit_cfg(
        tmp_path,
        round_timeout_per_phase={"dev": 3600, "qa": 1200},
        phases=["dev", "qa"],
    )
    assert _round_timeout_for(cfg, "dev") == 3600
    assert _round_timeout_for(cfg, "qa") == 1200


def test_given_phase_not_in_per_phase_dict_when_lookup_then_returns_global(
    tmp_path: Path,
) -> None:
    """Phase not in dict → fall back to global runtime.round_timeout_s."""
    cfg = _unit_cfg(
        tmp_path,
        round_timeout_per_phase={"dev": 3600},
        phases=["dev", "qa"],
    )
    assert _round_timeout_for(cfg, "qa") == 1800


def test_given_phase_none_when_lookup_then_returns_global(tmp_path: Path) -> None:
    """phase=None (no phases configured) → global timeout."""
    cfg = _unit_cfg(tmp_path)
    assert _round_timeout_for(cfg, None) == 1800


def test_given_round_log_contains_connection_refused_when_round_ends_then_emits_blip(
    tmp_path: Path,
) -> None:
    """A round whose log mentions a network pattern emits agent_network_blip."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir()
    log_path = rounds_dir / "R1-2026-05-13.log"
    log_path.write_text(
        "doing some work\nERROR: connection refused at api.anthropic.com\nretrying...\n"
    )

    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        round_num=1,
        phase="main",
        round_duration_s=12.5,
        exit_code=1,
        timed_out=False,
    )

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [p for p in payloads if p["event"] == "agent_network_blip"]
    assert len(blips) == 1
    assert blips[0]["round_num"] == 1
    assert blips[0]["phase"] == "main"
    assert "connection refused" in blips[0]["matched"].lower()
    assert blips[0]["round_duration_s"] == 12.5
    assert blips[0]["exit_code"] == 1
    assert blips[0]["timed_out"] is False


def test_given_round_log_clean_when_round_ends_then_no_blip_event(
    tmp_path: Path,
) -> None:
    """A round with no network patterns emits no agent_network_blip."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir()
    log_path = rounds_dir / "R2-2026-05-13.log"
    log_path.write_text("everything went fine\nno errors here\n")

    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        round_num=2,
        phase="main",
        round_duration_s=5.0,
        exit_code=0,
        timed_out=False,
    )

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    # File may not even exist if nothing was written
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        blips = [p for p in payloads if p["event"] == "agent_network_blip"]
        assert blips == []


def test_given_multiple_network_patterns_in_log_when_round_ends_then_emits_one_blip(
    tmp_path: Path,
) -> None:
    """Multiple distinct network patterns in one log → still one event (first match)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir()
    log_path = rounds_dir / "R3-2026-05-13.log"
    log_path.write_text("dns lookup failed\nlater: connection reset\nand: 502 bad gateway\n")

    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        round_num=3,
        phase="main",
        round_duration_s=30.0,
        exit_code=1,
        timed_out=False,
    )

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [p for p in payloads if p["event"] == "agent_network_blip"]
    assert len(blips) == 1
    assert "dns" in blips[0]["matched"].lower()
