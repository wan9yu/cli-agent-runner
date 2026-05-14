from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_runner.agent_runtime import RunResult
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

    result = RunResult(exit_code=1, duration_s=12.5, timed_out=False, pid=0)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=result,
        round_num=1,
        phase="main",
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

    result = RunResult(exit_code=0, duration_s=5.0, timed_out=False, pid=0)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=result,
        round_num=2,
        phase="main",
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

    result = RunResult(exit_code=1, duration_s=30.0, timed_out=False, pid=0)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=result,
        round_num=3,
        phase="main",
    )

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    blips = [p for p in payloads if p["event"] == "agent_network_blip"]
    assert len(blips) == 1
    assert "dns" in blips[0]["matched"].lower()


def test_given_clean_round_when_scan_called_then_no_blip_emitted(
    tmp_path: Path,
) -> None:
    """Clean rounds (exit 0, no timeout) skip log scan entirely."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir()
    log_path = rounds_dir / "R4-2026-05-13.log"
    log_path.write_text("ERROR: connection refused\n")  # would match if scanned

    result = RunResult(exit_code=0, duration_s=5.0, timed_out=False, pid=0)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=result,
        round_num=4,
        phase="main",
    )

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        blips = [p for p in payloads if p["event"] == "agent_network_blip"]
        assert blips == []


def test_given_holder_sidecar_present_when_lock_held_then_error_includes_pid_age_cmdline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LockHeldError message includes holder PID/age/cmdline when sidecar is fresh."""
    import json
    import os

    from agent_runner.runner import LockHeldError, _acquire_lock_or_raise

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock_path = log_dir / "agent-runner.lock"

    # Acquire lock once
    fd1 = _acquire_lock_or_raise(lock_path)

    # Sidecar should now exist
    sidecar = lock_path.parent / (lock_path.name + ".holder")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["pid"] == os.getpid()
    assert "started_at" in data
    assert "cmdline" in data

    # Try to acquire again → LockHeldError with holder info
    try:
        with pytest.raises(LockHeldError) as exc_info:
            _acquire_lock_or_raise(lock_path)
        msg = str(exc_info.value)
        assert f"PID {os.getpid()}" in msg
        assert "age" in msg
        assert "cmd:" in msg
    finally:
        os.close(fd1)
        sidecar.unlink(missing_ok=True)


def test_given_holder_sidecar_stale_when_lock_held_then_error_notes_stale(tmp_path: Path) -> None:
    """If sidecar references a non-existent PID, error notes 'stale sidecar'."""
    import json
    import os

    from agent_runner.runner import LockHeldError, _acquire_lock_or_raise

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock_path = log_dir / "agent-runner.lock"
    sidecar = lock_path.parent / (lock_path.name + ".holder")

    # Find a stale PID guaranteed not to exist
    fake_pid = 999999
    while True:
        try:
            os.kill(fake_pid, 0)
            fake_pid -= 1
        except (ProcessLookupError, PermissionError):
            break
        if fake_pid < 100:
            pytest.skip("could not find a stale PID for test")
            return

    # Acquire lock in this process — sidecar for THIS process is fresh
    fd1 = _acquire_lock_or_raise(lock_path)

    # Overwrite sidecar with stale data, then try second acquire
    sidecar.write_text(
        json.dumps(
            {
                "pid": fake_pid,
                "started_at": "2026-05-14T10:00:00.000Z",
                "cmdline": "agent-runner round",
            }
        )
    )

    try:
        with pytest.raises(LockHeldError) as exc_info:
            _acquire_lock_or_raise(lock_path)
        msg = str(exc_info.value)
        assert "stale" in msg.lower()
    finally:
        os.close(fd1)
        sidecar.unlink(missing_ok=True)


def test_given_holder_sidecar_missing_when_lock_held_then_error_notes_missing(
    tmp_path: Path,
) -> None:
    """If sidecar is missing (race / older version), error says 'holder unknown'."""
    import os

    from agent_runner.runner import LockHeldError, _acquire_lock_or_raise

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock_path = log_dir / "agent-runner.lock"
    sidecar = lock_path.parent / (lock_path.name + ".holder")

    fd1 = _acquire_lock_or_raise(lock_path)
    sidecar.unlink(missing_ok=True)  # simulate missing sidecar

    try:
        with pytest.raises(LockHeldError) as exc_info:
            _acquire_lock_or_raise(lock_path)
        msg = str(exc_info.value)
        assert "unknown" in msg.lower() or "missing" in msg.lower()
    finally:
        os.close(fd1)


def test_given_explicit_phase_when_phase_for_called_then_uses_override() -> None:
    """_phase_for honors explicit override over rotation counter."""
    from agent_runner.runner import _phase_for

    phases = ["dev", "qa", "product"]

    # Without override: rotation by round_num
    assert _phase_for(1, phases) == ("dev", 0)
    assert _phase_for(2, phases) == ("qa", 1)
    assert _phase_for(3, phases) == ("product", 2)

    # With override: explicit
    assert _phase_for(2, phases, override="product") == ("product", 2)
    assert _phase_for(7, phases, override="dev") == ("dev", 0)


def test_given_invalid_phase_override_when_phase_for_called_then_raises() -> None:
    """Override must match a name in phases list."""
    from agent_runner.runner import _phase_for

    with pytest.raises(ValueError, match="not in.*phases"):
        _phase_for(1, ["dev", "qa"], override="bogus")


def test_given_phase_override_no_phases_configured_when_phase_for_called_then_raises() -> None:
    """--phase requires [phases] to be configured."""
    from agent_runner.runner import _phase_for

    with pytest.raises(ValueError, match=r"\[phases\]"):
        _phase_for(1, None, override="dev")


def test_given_default_round_when_phase_for_called_then_rotation_unchanged() -> None:
    """Default rotation behavior preserved when override is None."""
    from agent_runner.runner import _phase_for

    phases = ["dev", "qa", "product"]
    assert _phase_for(4, phases) == ("dev", 0)  # rotation continues at (4-1) % 3 = 0


def test_given_hook_mutates_prompt_when_run_then_emits_prompt_overwritten(
    tmp_path: Path,
) -> None:
    """A PreRoundHook that mutates the prompt file triggers one event per mutation."""
    import json

    from agent_runner import hooks
    from agent_runner.runner import _run_pre_round_hooks

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("original prompt")

    class _MutatingHook:
        name = "mutating_test_hook"

        def before_round(self, ctx):
            prompt_file.write_text("new content from hook")

    hooks.register_pre_round_hook(_MutatingHook())
    try:
        ctx = hooks.HookContext(
            work_dir=tmp_path,
            log_dir=log_dir,
            project="t",
            round_num=5,
            phase="dev",
            agent_name=None,
        )
        _run_pre_round_hooks(ctx, log_dir, disabled=False, prompt_file=prompt_file)
    finally:
        hooks._PRE_ROUND_HOOKS[:] = [
            h for h in hooks._PRE_ROUND_HOOKS if h.name != "mutating_test_hook"
        ]

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    overwrites = [p for p in payloads if p["event"] == "prompt_overwritten"]
    assert len(overwrites) == 1
    assert overwrites[0]["hook"] == "mutating_test_hook"
    assert overwrites[0]["round_num"] == 5
    assert overwrites[0]["phase"] == "dev"
    assert overwrites[0]["old_hash"].startswith("sha256:")
    assert overwrites[0]["new_hash"].startswith("sha256:")
    assert overwrites[0]["old_hash"] != overwrites[0]["new_hash"]


def test_given_hook_no_op_when_run_then_no_prompt_overwritten(
    tmp_path: Path,
) -> None:
    """A hook that doesn't mutate the prompt emits nothing."""
    import json

    from agent_runner import hooks
    from agent_runner.runner import _run_pre_round_hooks

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("unchanged")

    class _NoOpHook:
        name = "noop_test_hook"

        def before_round(self, ctx):
            pass

    hooks.register_pre_round_hook(_NoOpHook())
    try:
        ctx = hooks.HookContext(
            work_dir=tmp_path,
            log_dir=log_dir,
            project="t",
            round_num=1,
            phase=None,
            agent_name=None,
        )
        _run_pre_round_hooks(ctx, log_dir, disabled=False, prompt_file=prompt_file)
    finally:
        hooks._PRE_ROUND_HOOKS[:] = [
            h for h in hooks._PRE_ROUND_HOOKS if h.name != "noop_test_hook"
        ]

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        overwrites = [p for p in payloads if p["event"] == "prompt_overwritten"]
        assert overwrites == []


def test_given_two_mutating_hooks_when_run_then_two_events_with_attribution(
    tmp_path: Path,
) -> None:
    """Multiple mutating hooks each produce a prompt_overwritten event."""
    import json

    from agent_runner import hooks
    from agent_runner.runner import _run_pre_round_hooks

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("original")

    class _HookA:
        name = "hook_a"

        def before_round(self, ctx):
            prompt_file.write_text("after A")

    class _HookB:
        name = "hook_b"

        def before_round(self, ctx):
            prompt_file.write_text("after A and B")

    hooks.register_pre_round_hook(_HookA())
    hooks.register_pre_round_hook(_HookB())
    try:
        ctx = hooks.HookContext(
            work_dir=tmp_path,
            log_dir=log_dir,
            project="t",
            round_num=2,
            phase=None,
            agent_name=None,
        )
        _run_pre_round_hooks(ctx, log_dir, disabled=False, prompt_file=prompt_file)
    finally:
        hooks._PRE_ROUND_HOOKS[:] = [
            h for h in hooks._PRE_ROUND_HOOKS if h.name not in ("hook_a", "hook_b")
        ]

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    overwrites = [p for p in payloads if p["event"] == "prompt_overwritten"]
    assert len(overwrites) == 2
    assert overwrites[0]["hook"] == "hook_a"
    assert overwrites[1]["hook"] == "hook_b"
    # Hash chaining: A's new_hash == B's old_hash
    assert overwrites[0]["new_hash"] == overwrites[1]["old_hash"]


def test_given_disable_pre_round_hooks_true_when_run_then_hooks_skipped(
    tmp_path: Path,
) -> None:
    """disable_pre_round_hooks = True skips all PreRoundHook invocations."""
    from agent_runner import hooks
    from agent_runner.runner import _run_pre_round_hooks

    hook_calls = []

    class _TestHook:
        name = "test_skip_target"

        def before_round(self, ctx):
            hook_calls.append(ctx.round_num)

    hooks.register_pre_round_hook(_TestHook())
    try:
        ctx = hooks.HookContext(
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
            project="t",
            round_num=1,
            phase=None,
            agent_name=None,
        )
        (tmp_path / "logs").mkdir()

        # disabled=True: hooks should NOT run
        _run_pre_round_hooks(ctx, tmp_path / "logs", disabled=True)
        assert hook_calls == [], "hooks should not run when disabled"

        # disabled=False: hooks SHOULD run
        _run_pre_round_hooks(ctx, tmp_path / "logs", disabled=False)
        assert hook_calls == [1], "hooks should run when not disabled"
    finally:
        hooks._PRE_ROUND_HOOKS[:] = [
            h for h in hooks._PRE_ROUND_HOOKS if h.name != "test_skip_target"
        ]
