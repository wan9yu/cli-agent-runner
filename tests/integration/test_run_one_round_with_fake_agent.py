"""End-to-end one-round test using the fake_agent_script fixture (5-line bash).

Covers: clean exit, dirty tree -> orphan stash, hang -> timeout kill, crash.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PhaseOverride,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.runner import run_one_round


def _cfg(tmp_git_repo: Path, fake_agent_script: Path, *, round_timeout_s: int = 30) -> Config:
    """Build a Config rooted in ``tmp_git_repo`` with prompt + .gitignore committed.

    Why commit before run: ``git stash push -u`` (used by the dirty path) sweeps
    untracked files. If we left ``p.md`` untracked it would vanish on the first
    stash, and any second round would crash in ``assemble_prompt`` with
    FileNotFoundError. Committing prompt + a ``.gitignore`` for ``logs/`` keeps
    the dirty-tree footprint to exactly the file the fake agent writes
    (``dirty.txt``), which matches the production-realistic case where the user's
    repo already has its config + prompt tracked and only agent-generated work is
    ever orphan-stashed.

    round_timeout_s defaults to a generous 30s that the "succeed"/"dirty"/"crash"
    rounds (which exit in milliseconds) never legitimately approach. History: this
    used to be a single shared 5s baked in here for every caller including the
    "hang" test. Under `-n auto` parallel contention (fork/exec scheduling delay,
    not agent_runtime logic) that 5s was occasionally eaten by "succeed"-style
    rounds too, SIGTERM-ing them mid-flight and flaking their exit-code asserts --
    confirmed by reproduction (`assert -15 == 137` on the "crash" round after a
    tight back-to-back `-n auto` stress loop). Splitting the knob removes that
    race for every caller that isn't testing timeout behavior; only the "hang"
    test below opts into a small value so it can still actually exercise the
    kill path.
    """
    log_dir = tmp_git_repo / "logs"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Test prompt body. " * 50)
    (tmp_git_repo / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
        cwd=tmp_git_repo,
        check=True,
    )
    return Config(
        agent=AgentConfig(command=[str(fake_agent_script)], prompt_arg_template=[]),
        runtime=RuntimeConfig(
            work_dir=tmp_git_repo, log_dir=log_dir, round_timeout_s=round_timeout_s
        ),
        prompt=PromptConfig(file=prompt, inject_context=True),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )


def test_round_should_mark_status_completed_when_fake_agent_succeeds(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "succeed")
    cfg = _cfg(tmp_git_repo, fake_agent_script)

    result = run_one_round(cfg)

    assert result.exit_code == 0
    assert not result.timed_out
    status = json.loads((cfg.runtime.log_dir / "status.json").read_text())
    assert status["last_exit_code"] == 0


def test_round_should_orphan_stash_dirty_tree_when_fake_agent_leaves_it_dirty(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "dirty")
    monkeypatch.setenv("WORK_DIR", str(tmp_git_repo))
    cfg = _cfg(tmp_git_repo, fake_agent_script)

    result = run_one_round(cfg)

    assert result.exit_code == 0
    assert result.stashed is True

    # Next round's round-context should mention the stash
    run_one_round(cfg)
    ctx = json.loads((cfg.runtime.log_dir / "round-context.json").read_text())
    assert "orphan_stash" in ctx
    assert ctx["orphan_stash"]["ref"].startswith("")  # SHA, not stash@{N}


def test_round_should_be_killed_within_grace_when_fake_agent_hangs_past_timeout(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "hang")
    # This is the one caller that WANTS a small round_timeout_s -- it's the
    # only test in this file actually exercising the timeout-kill path.
    cfg = _cfg(tmp_git_repo, fake_agent_script, round_timeout_s=5)

    start = time.time()
    result = run_one_round(cfg)
    elapsed = time.time() - start

    assert result.timed_out is True
    # Baseline ~5s (round_timeout_s=5; REAP_GRACE_S is never paid -- SIGTERM
    # kills bash+sleep immediately). Widened from 15 to 30 (2x, matching the
    # analogous pi e2e bound in test_e2e_round_lifecycle.py) after a real
    # one-off flake under `-n auto` parallel contention: fork/exec + scheduler
    # jitter across many workers' subprocesses can eat several extra seconds
    # without the kill itself having failed. Still miles below the fake
    # agent's un-escalated `sleep 9999`, so a genuine hang (kill never fires)
    # still fails loudly.
    assert elapsed < 30

    events_files = list(cfg.runtime.log_dir.glob("events-*.jsonl"))
    events = [json.loads(line) for line in events_files[0].read_text().splitlines()]
    assert any(e["event"] == "round_timeout_kill" for e in events)


def test_round_should_propagate_exit_code_when_fake_agent_crashes(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "crash")
    cfg = _cfg(tmp_git_repo, fake_agent_script)

    result = run_one_round(cfg)

    assert result.exit_code == 137


def test_round_should_apply_phase_override_timeout_when_phase_has_override(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    """[phases.dev] round_timeout_s = 3600 controls subprocess kill timing, not the default."""
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "succeed")
    cfg = _cfg(tmp_git_repo, fake_agent_script)
    # Rebuild cfg with per-phase override: global=30 (the _cfg() default), dev override=3600
    import dataclasses

    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["dev"],
            overrides={"dev": PhaseOverride(round_timeout_s=3600)},
        ),
    )

    captured_timeout: list[int] = []
    import agent_runner.agent_runtime as agent_runtime_mod

    original_run = agent_runtime_mod.run

    def capturing_run(**kwargs):
        captured_timeout.append(kwargs["timeout_s"])
        # cwd= spawn wiring: runner must thread cfg.runtime.work_dir through
        assert kwargs["work_dir"] == cfg.runtime.work_dir
        return original_run(**kwargs)

    monkeypatch.setattr(agent_runtime_mod, "run", capturing_run)

    result = run_one_round(cfg, phase_override="dev")

    assert result.exit_code == 0
    assert not result.timed_out
    assert captured_timeout, "agent_runtime.run was never called"
    # Resolved timeout should be the per-phase override (3600), not the global (30)
    assert captured_timeout[0] == 3600, (
        f"Expected resolved timeout 3600 (phase override), got {captured_timeout[0]}"
    )


def test_round_should_pass_agent_env_to_subprocess_when_cfg_has_env(
    tmp_git_repo: Path,
) -> None:
    """cfg.agent.env reaches the subprocess as env_extra -- no implicit injection."""
    script = tmp_git_repo / "env-check-agent.sh"
    record = tmp_git_repo / "env-record.txt"
    script.write_text(
        f'#!/usr/bin/env bash\necho "MY_FLAG=${{MY_FLAG:-unset}}" > "{record}"\nexit 0\n'
    )
    script.chmod(0o755)

    log_dir = tmp_git_repo / "logs"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Test prompt body. " * 50)
    (tmp_git_repo / ".gitignore").write_text("logs/\nenv-record.txt\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
        cwd=tmp_git_repo,
        check=True,
    )

    cfg = Config(
        agent=AgentConfig(
            command=[str(script)],
            prompt_arg_template=[],
            env={"MY_FLAG": "passed-through"},
        ),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir, round_timeout_s=30),
        prompt=PromptConfig(file=prompt, inject_context=False),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )

    run_one_round(cfg)

    assert record.exists(), "fake agent didn't run -- check runner spawn path"
    assert "MY_FLAG=passed-through" in record.read_text()


def test_round_command_should_succeed_when_launched_from_a_different_cwd_with_relative_paths(
    tmp_path: Path,
) -> None:
    """Launching `agent-runner round` from CWD ≠ work_dir with relative TOML paths
    must succeed (regression for 0.1.16 path-resolution bug)."""
    import sys

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / "prompts").mkdir()
    # Prompt must be >= 500 bytes to pass prompt_smoke_passes startup check
    (work_dir / "prompts" / "p.md").write_text("Substantive prompt content. " * 25)
    (work_dir / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        'log_dir = "logs"\n'
        "[prompt]\n"
        'files = ["prompts/p.md"]\n'
    )
    # work_dir must be a git repo to pass work_dir_is_git_repo startup check
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=work_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=work_dir,
        check=True,
    )

    # Launch round subprocess from a DIFFERENT cwd (the original repro)
    other_cwd = tmp_path
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(work_dir / "agent-runner.toml"),
            "round",
        ],
        cwd=other_cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"round failed: stderr={result.stderr}"
    assert "STARTUP FAIL" not in result.stderr
