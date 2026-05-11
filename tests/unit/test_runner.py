from __future__ import annotations

import json
import shutil
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.runner import run_one_round


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
    tmp_git_repo: Path, fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    result = run_one_round(cfg)
    assert result.exit_code == 0
    status = json.loads((cfg.runtime.log_dir / "status.json").read_text())
    assert status["round_num"] == 1
    assert status["last_exit_code"] == 0


def test_given_three_runs_when_invoked_sequentially_then_round_num_increments(
    tmp_git_repo: Path, fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    for expected in (1, 2, 3):
        run_one_round(cfg)
        status = json.loads((cfg.runtime.log_dir / "status.json").read_text())
        assert status["round_num"] == expected


def test_given_phases_configured_when_run_then_phase_in_round_context(
    tmp_git_repo: Path, fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script, phases=["a", "b"])
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert ctx["phase"] == "a"
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert ctx["phase"] == "b"


def test_given_phases_unconfigured_when_run_then_phase_field_absent(
    tmp_git_repo: Path, fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script, phases=None)
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert "phase" not in ctx


def test_given_corrupt_status_when_run_then_recovers_and_emits_event(
    tmp_git_repo: Path, fake_agent_script: Path,
) -> None:
    cfg = _make_config(tmp_git_repo, fake_agent_script)
    cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.runtime.log_dir / "status.json").write_text("{ corrupt")
    run_one_round(cfg)
    events_files = list(cfg.runtime.log_dir.glob("events-*.jsonl"))
    assert events_files, "events.jsonl should have been written"
    events = [json.loads(line) for line in events_files[0].read_text().splitlines()]
    assert any(e["event"] == "status_recovered" for e in events)
