from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner.agent_runtime import RunResult
from agent_runner.config import (
    AgentConfig,
    Config,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)


def _make_mock_runtime(fake_run):  # type: ignore[no-untyped-def]
    """Build a minimal agent_runtime mock object with the given fake_run callable."""
    from agent_runner import agent_runtime as _ar

    return type("M", (), {"run": staticmethod(fake_run), "RunResult": _ar.RunResult})()


def test_run_one_round_inner_should_call_stash_orphan_when_dirty_action_is_stash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner import runner, vcs_state

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")

    def fake_run(**_kwargs):
        (tmp_path / "scratch.md").write_text("from agent")
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner, "agent_runtime", _make_mock_runtime(fake_run))

    stash_calls = []

    def fake_stash(*args, **kwargs):
        stash_calls.append(kwargs)
        from agent_runner.vcs_state import StashRef

        return StashRef(sha="fake_sha", message="ORPHAN R1")

    # resolve_dirty_tree calls stash_orphan as a same-module name, so the patch
    # target is vcs_state (where it resolves), not the api re-export facade.
    monkeypatch.setattr(vcs_state, "stash_orphan", fake_stash)
    monkeypatch.setattr(vcs_state, "detect_dirty_files", lambda _w: ["scratch.md"])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(dirty_action="stash"),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    assert len(stash_calls) == 1, "stash_orphan should be called once for stash mode"


def test_run_one_round_inner_should_run_goal_checks_before_stash_removes_the_edit_when_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    """Pins the call-site placement: a goal check must observe the round's own
    edit BEFORE vcs_state.resolve_dirty_tree's real `git stash -u` removes it
    (dirty_action="stash") -- a future refactor that moved the call below
    resolve_dirty_tree would flip this check's `satisfied` to False, catching
    the regression. Uses a REAL git repo/stash (no vcs_state mocking) so the
    ordering is genuinely exercised, not merely asserted."""
    from agent_runner import runner
    from agent_runner.config import GoalConfig, _GoalCheckConfig

    log_dir = tmp_git_repo.parent / f"goal-order-logs-{tmp_git_repo.name}"
    log_dir.mkdir()
    prompt = tmp_git_repo.parent / f"goal-order-prompt-{tmp_git_repo.name}.md"
    prompt.write_text("hi")

    def fake_run(**_kwargs):
        (tmp_git_repo / "edited.txt").write_text("from agent")
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner, "agent_runtime", _make_mock_runtime(fake_run))

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(dirty_action="stash"),
        phases=PhasesConfig(),
        goal=GoalConfig(
            checks=(_GoalCheckConfig(name="edited", cmd=["sh", "-c", "test -f edited.txt"]),),
            ledger="ledger.md",
        ),
    )

    round_result = runner._run_one_round_inner(cfg)

    assert round_result.stashed is True, "the real git stash must have run"
    assert not (tmp_git_repo / "edited.txt").exists(), "stash should have removed the edit"
    from tests._test_helpers import read_events_for_current_month

    goal_events = [e for e in read_events_for_current_month(log_dir) if e["event"] == "goal_check"]
    assert len(goal_events) == 1
    assert goal_events[0]["satisfied"] is True, (
        "goal check ran AFTER the stash removed the edit -- placement regressed"
    )


def test_run_one_round_inner_should_skip_stash_when_dirty_action_is_ignore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner import runner, vcs_state

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")

    def fake_run(**_kwargs):
        (tmp_path / "scratch.md").write_text("from agent")
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner, "agent_runtime", _make_mock_runtime(fake_run))

    stash_calls = []
    monkeypatch.setattr(vcs_state, "stash_orphan", lambda *a, **k: stash_calls.append(k))
    monkeypatch.setattr(vcs_state, "detect_dirty_files", lambda _w: ["scratch.md"])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(dirty_action="ignore"),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    assert stash_calls == [], "stash_orphan must NOT be called for ignore mode"
    all_events = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert any(e["event"] == "dirty_detected" for e in all_events)


def test_run_one_round_inner_should_create_git_commit_when_dirty_action_is_auto_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import runner

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")

    def fake_run(**_kwargs):
        (tmp_path / "scratch.md").write_text("from agent")
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner, "agent_runtime", _make_mock_runtime(fake_run))

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(dirty_action="auto_commit"),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path, capture_output=True, text=True
    )
    assert log.stdout.strip() == "agent-runner auto-commit: R1"


def test_run_one_round_inner_should_emit_dirty_commit_failed_when_git_identity_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import runner

    # Git repo with empty user.email + user.name → commit fails regardless of global config
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", ""], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", ""], cwd=tmp_path, check=True)
    (tmp_path / ".gitkeep").write_text("")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")

    def fake_run(**_kwargs):
        (tmp_path / "scratch.md").write_text("from agent")
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner, "agent_runtime", _make_mock_runtime(fake_run))

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(dirty_action="auto_commit"),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    all_events = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    failed = [e for e in all_events if e["event"] == "dirty_commit_failed"]
    assert len(failed) == 1
    assert failed[0]["round_num"] == 1
    assert "reason" in failed[0]


def test_run_one_round_inner_should_pass_round_num_in_env_to_agent_subprocess_when_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner import runner

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")
    captured_env: dict[str, str] = {}

    def fake_run(*, env_extra, **_kwargs):
        captured_env.update(env_extra)
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner.agent_runtime, "run", fake_run)
    monkeypatch.setattr(runner.vcs_state, "detect_dirty_files", lambda _w: [])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    assert captured_env.get("AGENT_RUNNER_ROUND_NUM") == "1"
    assert captured_env.get("AGENT_RUNNER_LOG_DIR") == str(log_dir)


def test_run_one_round_inner_should_set_env_phase_empty_when_phases_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner import runner

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")
    captured_env: dict[str, str] = {}

    def fake_run(*, env_extra, **_kwargs):
        captured_env.update(env_extra)
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner.agent_runtime, "run", fake_run)
    monkeypatch.setattr(runner.vcs_state, "detect_dirty_files", lambda _w: [])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(),
        phases=PhasesConfig(list=None),
    )

    runner._run_one_round_inner(cfg)

    assert captured_env.get("AGENT_RUNNER_PHASE") == ""


def test_run_one_round_inner_should_set_env_phase_to_rotation_result_when_phases_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner import runner

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")
    captured_env: dict[str, str] = {}

    def fake_run(*, env_extra, **_kwargs):
        captured_env.update(env_extra)
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner.agent_runtime, "run", fake_run)
    monkeypatch.setattr(runner.vcs_state, "detect_dirty_files", lambda _w: [])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(),
        phases=PhasesConfig(list=["diverge", "converge"]),
    )

    runner._run_one_round_inner(cfg)

    # round_num = 1 (no prior status), phase_for(1, ["diverge","converge"]) = "diverge"
    assert captured_env.get("AGENT_RUNNER_PHASE") == "diverge"


def test_run_one_round_inner_should_fire_post_round_hook_after_round_end_event_when_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PostRoundHook runs AFTER round_end is emitted, not before.

    Four docs stated the opposite for the life of the hook. Pin the real order:
    the hook observes round_end already on disk.
    """
    from agent_runner import hooks, runner
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")
    seen: list[list[str]] = []

    class _Probe:
        name = "order_probe"

        def after_round(self, ctx, result) -> None:
            seen.append([e["event"] for e in read_events_for_current_month(log_dir)])

    def fake_run(**_kwargs):
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner.agent_runtime, "run", fake_run)
    monkeypatch.setattr(runner.vcs_state, "detect_dirty_files", lambda _w: [])
    monkeypatch.setattr(hooks, "_POST_ROUND_HOOKS", [_Probe()])

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=log_dir),
        prompt=PromptConfig(file=prompt),
        vcs=VcsConfig(),
        phases=PhasesConfig(),
    )

    runner._run_one_round_inner(cfg)

    assert seen, "post_round hook never fired"
    assert "round_end" in seen[-1], (
        "PostRoundHook ran before round_end was emitted — hooks.py:149's claim"
    )
