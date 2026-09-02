from __future__ import annotations

import os
from pathlib import Path

import pytest

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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file-permission checks")
def test_run_one_round_reaches_sys_exit_76_on_genuinely_unwritable_log_dir(
    tmp_git_repo: Path,
) -> None:
    """Reachability regression (Group A / spec-review critical): log_dir_writable is the
    SOLE permanent=False battery check, but events.emit's own append can raise on
    the EXACT unwritable-log_dir condition it is reporting -- BEFORE the
    sys.exit(76) below it ever runs -- because open(path, "a") only needs write on
    an EXISTING file, not on the directory. A pre-existing events-<month>.jsonl
    would mask the bug (append still succeeds under a 555 dir), so this uses a
    FRESH log_dir with no prior events file, reproducing the unguarded-emit crash
    pre-fix and the clean SystemExit(76) post-fix."""
    from agent_runner import runner
    from agent_runner.config import AgentConfig, Config, PromptConfig, RuntimeConfig, VcsConfig

    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    prompt_file = tmp_git_repo / "p.md"
    prompt_file.write_text("Long prompt body for testing." * 20)
    cfg = Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir),
        prompt=PromptConfig(file=prompt_file, inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
    log_dir.chmod(0o555)  # genuinely unwritable, no events-*.jsonl exists yet
    try:
        with pytest.raises(SystemExit) as ei:
            runner.run_one_round(cfg)
        assert ei.value.code == ENV_BATTERY_EXIT
    finally:
        log_dir.chmod(0o755)  # tmp_path teardown needs write back
