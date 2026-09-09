"""Tests for serve_startup_hooks registry + Protocol."""

from __future__ import annotations

import pytest

from tests._test_helpers import FakeArgs, make_toml


def test_serve_startup_hooks_should_return_empty_list_when_no_hooks_registered() -> None:
    from agent_runner.hooks import serve_startup_hooks

    result = serve_startup_hooks()

    assert isinstance(result, list)


def test_serve_startup_hooks_should_return_hooks_in_registration_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runner import hooks

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    class HookA:
        name = "hook_a"

        def __call__(self, cfg) -> None:
            pass

    class HookB:
        name = "hook_b"

        def __call__(self, cfg) -> None:
            pass

    hooks.register_serve_startup_hook(HookA())
    hooks.register_serve_startup_hook(HookB())

    out = hooks.serve_startup_hooks()

    assert [h.name for h in out] == ["hook_a", "hook_b"]


def test_register_serve_startup_hook_should_raise_when_name_is_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_unique pattern: re-registering same name raises."""
    from agent_runner import hooks

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    class HookA:
        name = "dup"

        def __call__(self, cfg) -> None:
            pass

    hooks.register_serve_startup_hook(HookA())

    with pytest.raises(ValueError, match="dup"):
        hooks.register_serve_startup_hook(HookA())


def test_serve_startup_hook_protocol_should_be_satisfied_by_class_with_name_and_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ServeStartupHook is runtime_checkable; classes with name + __call__ satisfy."""
    from agent_runner.hooks import ServeStartupHook

    class GoodHook:
        name = "good"

        def __call__(self, cfg) -> None:
            pass

    assert isinstance(GoodHook(), ServeStartupHook)


def test_serve_cmd_should_proceed_to_loop_when_startup_hook_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Hook runs successfully; serve proceeds to PID file write + loop."""
    from agent_runner import hooks
    from agent_runner.cli import serve_cmd

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    called = {"count": 0}

    class GoodHook:
        name = "good_hook"

        def __call__(self, cfg) -> None:
            called["count"] += 1

    hooks.register_serve_startup_hook(GoodHook())

    cfg_path = make_toml(tmp_path)

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        round_log_path.write_text("")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert called["count"] == 1


def test_serve_cmd_should_abort_and_emit_failure_event_when_startup_hook_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Serve aborts with exit 78 (deterministic, no retry -- Group A)
    and emits serve_startup_hook_failed."""
    import json
    import subprocess

    from agent_runner import hooks
    from agent_runner._serve_policy import PERMANENT_CONFIG_EXIT
    from agent_runner.cli import serve_cmd

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    class BadHook:
        name = "bad_hook"

        def __call__(self, cfg) -> None:
            raise RuntimeError("seeding failed: disk full")

    hooks.register_serve_startup_hook(BadHook())

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    called = {"subprocess_run": 0}

    def fake_run(*_args, **_kwargs):
        called["subprocess_run"] += 1

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == PERMANENT_CONFIG_EXIT
    assert called["subprocess_run"] == 0

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "expected event file to exist"
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [p for p in payloads if p["event"] == "serve_startup_hook_failed"]
    assert len(failed) == 1
    assert failed[0]["hook"] == "bad_hook"
    assert failed[0]["exc_type"] == "RuntimeError"
    assert "disk full" in failed[0]["exc_msg"]


def test_serve_cmd_should_skip_later_hooks_when_earlier_hook_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import subprocess

    from agent_runner import hooks
    from agent_runner._serve_policy import PERMANENT_CONFIG_EXIT
    from agent_runner.cli import serve_cmd

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    called_b = {"count": 0}

    class BadA:
        name = "bad_a"

        def __call__(self, cfg) -> None:
            raise RuntimeError("first hook fails")

    class GoodB:
        name = "good_b"

        def __call__(self, cfg) -> None:
            called_b["count"] += 1

    hooks.register_serve_startup_hook(BadA())
    hooks.register_serve_startup_hook(GoodB())

    cfg_path = make_toml(tmp_path)

    def fake_run(*_args, **_kwargs):
        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == PERMANENT_CONFIG_EXIT
    assert called_b["count"] == 0
