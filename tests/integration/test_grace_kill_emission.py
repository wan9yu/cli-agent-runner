"""Integration: round_grace_kill event reaches events.jsonl."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.runner import run_one_round
from tests._test_helpers import read_events_for_current_month


def _init_git(work_dir: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=work_dir, check=True)
    gitignore = work_dir / ".gitignore"
    gitignore.write_text("logs/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=work_dir,
        check=True,
    )


def _make_grace_config(work_dir: Path, script_path: Path, grace_s: int) -> Config:
    log_dir = work_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt = work_dir / "p.md"
    prompt.write_text("Test prompt. " * 50)
    return Config(
        agent=AgentConfig(command=[str(script_path)], prompt_arg_template=[]),
        runtime=RuntimeConfig(
            work_dir=work_dir,
            log_dir=log_dir,
            round_timeout_s=10,
            max_grace_after_result_s=grace_s,
        ),
        prompt=PromptConfig(file=prompt, inject_context=False),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )


def test_grace_kill_emits_round_grace_kill_event(tmp_path: Path) -> None:
    """Full runner flow: subprocess emits result then hangs; round_grace_kill event fires."""
    _init_git(tmp_path)

    script = tmp_path / "agent.sh"
    script.write_text(
        '#!/bin/bash\necho \'{"type":"result","is_error":false}\'\nexec sleep 10\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    cfg = _make_grace_config(tmp_path, script, grace_s=1)
    result = run_one_round(cfg)

    assert result.killed_for_grace is True
    assert result.timed_out is True

    events = read_events_for_current_month(cfg.runtime.log_dir)
    grace_events = [e for e in events if e.get("event") == "round_grace_kill"]
    assert len(grace_events) == 1
    assert grace_events[0]["round_num"] == 1
    assert grace_events[0]["grace_s"] == 1

    # round_timeout_kill must NOT appear (grace kill is distinct)
    timeout_events = [e for e in events if e.get("event") == "round_timeout_kill"]
    assert len(timeout_events) == 0


def _make_grace_config_with_patterns(
    work_dir: Path, script_path: Path, grace_s: int, patterns: list[str]
) -> Config:
    log_dir = work_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt = work_dir / "p.md"
    prompt.write_text("Test prompt. " * 50)
    return Config(
        agent=AgentConfig(command=[str(script_path)], prompt_arg_template=[]),
        runtime=RuntimeConfig(
            work_dir=work_dir,
            log_dir=log_dir,
            round_timeout_s=10,
            max_grace_after_result_s=grace_s,
            grace_kill_ignore_patterns=patterns,
        ),
        prompt=PromptConfig(file=prompt, inject_context=False),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )


def test_round_grace_extended_emitted_when_worker_alive(tmp_path: Path) -> None:
    """Full runner flow: subprocess emits result then backgrounds a long child;
    round_grace_extended event fires (not round_grace_kill); wall timeout reaps."""
    _init_git(tmp_path)

    script = tmp_path / "agent.sh"
    script.write_text(
        '#!/bin/bash\necho \'{"type":"result","is_error":false}\'\nsleep 30 &\nwait\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    cfg = _make_grace_config(tmp_path, script, grace_s=1)
    result = run_one_round(cfg)

    assert result.killed_for_grace is False  # spared by liveness
    assert result.timed_out is True  # wall-clock ceiling reaped it

    events = read_events_for_current_month(cfg.runtime.log_dir)

    # round_grace_extended must appear with live_children populated
    extended_events = [e for e in events if e.get("event") == "round_grace_extended"]
    assert len(extended_events) == 1
    assert extended_events[0]["round_num"] == 1
    assert extended_events[0]["grace_s"] == 1
    assert any("sleep" in c for c in extended_events[0]["live_children"])

    # round_grace_kill must NOT appear (round was busy, not idle)
    grace_kill_events = [e for e in events if e.get("event") == "round_grace_kill"]
    assert len(grace_kill_events) == 0


def test_round_grace_extended_carries_ignored_children(tmp_path: Path) -> None:
    """With grace_kill_ignore_patterns set, persistent helpers appear under
    ignored_children, not live_children — even when a real worker is also alive."""
    _init_git(tmp_path)

    script = tmp_path / "agent.sh"
    # Emit result, then background both a snapshot-like helper and a 'real' sleep.
    # exec -a renames the subprocess's argv[0] so the pattern can match it.
    script.write_text(
        "#!/bin/bash\n"
        'echo \'{"type":"result","is_error":false}\'\n'
        "exec -a snapshot-bash-test sleep 30 &\n"
        "sleep 30 &\n"
        "wait\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    cfg = _make_grace_config_with_patterns(
        tmp_path, script, grace_s=1, patterns=["snapshot-bash-test"]
    )
    result = run_one_round(cfg)

    assert result.killed_for_grace is False  # real worker kept it alive
    assert result.timed_out is True  # wall-clock reaped it

    events_list = read_events_for_current_month(cfg.runtime.log_dir)
    extended_events = [e for e in events_list if e.get("event") == "round_grace_extended"]
    assert len(extended_events) == 1
    ev = extended_events[0]

    # The plain sleep goes to live_children (real worker)
    assert any("sleep" in c for c in ev["live_children"])
    # The exec -a snapshot-bash-test process goes to ignored_children
    assert any("snapshot-bash-test" in c for c in ev["ignored_children"])

    # round_grace_kill must NOT appear (real worker still alive)
    grace_kill_events = [e for e in events_list if e.get("event") == "round_grace_kill"]
    assert len(grace_kill_events) == 0
