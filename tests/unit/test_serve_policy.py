"""_serve_policy is the canonical home of the serve restart policy; api re-exports it."""

from __future__ import annotations


def test_serve_policy_exports_constants_and_decision() -> None:
    from agent_runner import _serve_policy as sp

    assert sp.PERMANENT_CONFIG_EXIT == 78
    assert sp.CRASH_LOOP_EXIT == 75
    assert sp.ENV_BATTERY_EXIT == 76
    action, _, n = sp.post_round_decision(
        returncode=sp.PERMANENT_CONFIG_EXIT,
        duration_s=1.0,
        throttle_active=False,
        consecutive=0,
        restart_delay_s=5,
    )
    assert action == "config_broken"


def test_api_facade_re_exports_the_same_policy_objects() -> None:
    from agent_runner import _serve_policy as sp
    from agent_runner import api

    assert api.post_round_decision is sp.post_round_decision
    assert api.PERMANENT_CONFIG_EXIT is sp.PERMANENT_CONFIG_EXIT
    assert api.CRASH_LOOP_EXIT is sp.CRASH_LOOP_EXIT
    assert api.ENV_BATTERY_EXIT is sp.ENV_BATTERY_EXIT


def test_mem_loop_decision_increments_to_threshold() -> None:
    from agent_runner._serve_policy import MEM_LOOP_THRESHOLD, _mem_loop_decision

    c = 0
    action = "continue"
    for _ in range(MEM_LOOP_THRESHOLD):
        action, c = _mem_loop_decision(mem_terminated=True, consecutive=c)
    assert action == "mem_loop" and c == MEM_LOOP_THRESHOLD


def test_mem_loop_decision_non_mem_round_resets() -> None:
    from agent_runner._serve_policy import _mem_loop_decision

    action, c = _mem_loop_decision(mem_terminated=True, consecutive=2)
    assert action == "continue" and c == 3
    action, c = _mem_loop_decision(mem_terminated=False, consecutive=3)
    assert action == "continue" and c == 0


def test_mem_loop_exit_value_and_restartable(tmp_path) -> None:
    from agent_runner._serve_policy import CRASH_LOOP_EXIT, MEM_LOOP_EXIT, PERMANENT_CONFIG_EXIT
    from agent_runner.config import AgentConfig, Config, PromptConfig, RuntimeConfig, VcsConfig
    from agent_runner.service_unit import render_serve_unit

    assert MEM_LOOP_EXIT == 71
    cfg = Config(
        agent=AgentConfig(command=["my-agent"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=tmp_path / "p.md", inject_context=True),
        vcs=VcsConfig(),
    )
    unit = render_serve_unit(
        cfg, script_path=tmp_path / "ar", config_path=tmp_path / "agent-runner.toml"
    )
    assert f"RestartPreventExitStatus={PERMANENT_CONFIG_EXIT} {CRASH_LOOP_EXIT}" in unit
    restart_line = [ln for ln in unit.splitlines() if ln.startswith("RestartPreventExitStatus=")][0]
    assert str(MEM_LOOP_EXIT) not in restart_line.split("=", 1)[1].split()


def test_mem_loop_persistent_exit_value_and_free() -> None:
    """0.2.16 Task 5: MEM_LOOP_PERSISTENT_EXIT is a distinct sysexits-band
    code from every other serve give-up/restart exit code in use."""
    from agent_runner._serve_policy import (
        CRASH_LOOP_EXIT,
        ENV_BATTERY_EXIT,
        MEM_LOOP_EXIT,
        MEM_LOOP_PERSISTENT_EXIT,
        PERMANENT_CONFIG_EXIT,
    )

    assert MEM_LOOP_PERSISTENT_EXIT == 70
    assert MEM_LOOP_PERSISTENT_EXIT not in (
        0,
        1,
        130,
        PERMANENT_CONFIG_EXIT,
        CRASH_LOOP_EXIT,
        ENV_BATTERY_EXIT,
        MEM_LOOP_EXIT,
    )
