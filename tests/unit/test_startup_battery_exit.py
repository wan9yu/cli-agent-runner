from __future__ import annotations

from agent_runner.api import ENV_BATTERY_EXIT, PERMANENT_CONFIG_EXIT, post_round_decision
from agent_runner.startup_check import CheckResult, battery_exit_code


def test_permanent_failure_maps_to_78() -> None:
    fs = [CheckResult("a", ok=False, permanent=True), CheckResult("b", ok=False)]
    assert battery_exit_code(fs) == PERMANENT_CONFIG_EXIT


def test_only_environmental_maps_to_76() -> None:
    assert battery_exit_code([CheckResult("b", ok=False)]) == ENV_BATTERY_EXIT


def test_env_battery_exit_never_trips_crash_loop() -> None:
    # 76 is treated like an active throttle: continue, breaker disarmed, even
    # across many fast rounds — a ~5-round environmental blip must not hit 75.
    consecutive = 0
    for _ in range(6):
        action, delay, consecutive = post_round_decision(
            returncode=ENV_BATTERY_EXIT,
            duration_s=0.1,
            throttle_active=False,
            consecutive=consecutive,
            restart_delay_s=3,
        )
        assert action == "continue"
    assert (delay, consecutive) == (6, 0)  # doubled restart delay, breaker disarmed


def test_run_one_round_exits_76_on_environmental_failure(monkeypatch, tmp_path):
    import pytest

    from agent_runner import runner, startup_check
    from agent_runner.config import AgentConfig, Config, PromptConfig, RuntimeConfig, VcsConfig

    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=tmp_path / "p.md"),
        vcs=VcsConfig(),
        phases=None,
    )
    monkeypatch.setattr(
        startup_check,
        "run_battery",
        lambda _c: [startup_check.CheckResult("log_dir_writable", False, "ENOSPC")],
    )
    with pytest.raises(SystemExit) as ei:
        runner.run_one_round(cfg)
    assert ei.value.code == ENV_BATTERY_EXIT
