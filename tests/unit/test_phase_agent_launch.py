"""Per-round model rotation: the runner must launch the *phase's* agent.

0.2.9 Task 2 — `Config.profile_for(phase).agent` drives the round path (command,
prompt args, delivery, env) and the hook/metric binary. The startup battery
validates every profile's `command[0]`, so a bad phase agent fails before its
round instead of silent-burning it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import hooks, runner
from agent_runner.agent_runtime import RunResult
from agent_runner.config import (
    AgentConfig,
    Config,
    PhaseOverride,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)


def _cfg(tmp_git_repo: Path, base_cmd: list[str], phase_b_cmd: list[str]) -> Config:
    sandbox = tmp_git_repo.parent / f"phase-agent-{tmp_git_repo.name}"
    sandbox.mkdir(exist_ok=True)
    log_dir = sandbox / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt = sandbox / "prompt.md"
    prompt.write_text("Test prompt body. " * 50)
    return Config(
        agent=AgentConfig(command=base_cmd, prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir, round_timeout_s=10),
        prompt=PromptConfig(file=prompt, inject_context=True),
        vcs=VcsConfig(dirty_action="ignore"),
        phases=PhasesConfig(
            list=["a", "b"],
            overrides={
                "b": PhaseOverride(agent=AgentConfig(command=phase_b_cmd, prompt_arg_template=[])),
            },
        ),
    )


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_run(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return RunResult(exit_code=0, duration_s=1.0, timed_out=False, pid=0)

    monkeypatch.setattr(runner.agent_runtime, "run", fake_run)
    return calls


def _capture_hook_binary(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Register a pre-round hook that records the HookContext's agent_binary —
    the observable the runner derives from the phase's own agent command."""
    seen: list[str | None] = []

    class _CaptureHook:
        name = "capture_agent_binary"

        def before_round(self, ctx):  # type: ignore[no-untyped-def]
            seen.append(ctx.agent_binary)

    # Isolate the registry so the capture hook is auto-removed on teardown.
    monkeypatch.setattr(hooks, "_PRE_ROUND_HOOKS", [], raising=False)
    hooks.register_pre_round_hook(_CaptureHook())
    return seen


def test_given_phase_override_agent_when_round_runs_then_launches_phase_agent(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_git_repo, base_cmd=["base-agent"], phase_b_cmd=["glm-cli"])
    calls = _capture_run(monkeypatch)
    seen = _capture_hook_binary(monkeypatch)

    runner._run_one_round_inner(cfg, phase_override="b")

    assert calls[-1]["command"] == ["glm-cli"]
    assert seen == ["glm-cli"]


def test_given_phase_without_override_when_round_runs_then_launches_base_agent(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_git_repo, base_cmd=["base-agent"], phase_b_cmd=["glm-cli"])
    calls = _capture_run(monkeypatch)
    seen = _capture_hook_binary(monkeypatch)

    runner._run_one_round_inner(cfg, phase_override="a")

    assert calls[-1]["command"] == ["base-agent"]
    assert seen == ["base-agent"]


def test_given_bad_phase_agent_when_battery_runs_then_cli_check_fails(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.startup_check import run_battery

    cfg = _cfg(tmp_git_repo, base_cmd=["bash"], phase_b_cmd=["definitely-nonexistent-cli-xyz"])
    failures = [r for r in run_battery(cfg) if not r.ok]
    assert any(r.name.startswith("agent_cli_in_path") for r in failures), failures
