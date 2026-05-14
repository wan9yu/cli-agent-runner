"""Tests for serve_startup_hooks registry + Protocol."""

from __future__ import annotations

import pytest

from tests._test_helpers import make_toml


def test_given_no_registered_hooks_when_serve_startup_hooks_then_empty_list() -> None:
    from agent_runner.hooks import serve_startup_hooks

    result = serve_startup_hooks()
    assert isinstance(result, list)


def test_given_hook_registered_when_serve_startup_hooks_then_returned_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register_serve_startup_hook adds to registry; order is registration order."""
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


def test_given_duplicate_name_when_register_serve_startup_hook_then_raises(
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


def test_given_protocol_then_runtime_checkable_class_satisfies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ServeStartupHook is runtime_checkable; classes with name + __call__ satisfy."""
    from agent_runner.hooks import ServeStartupHook

    class GoodHook:
        name = "good"

        def __call__(self, cfg) -> None:
            pass

    assert isinstance(GoodHook(), ServeStartupHook)


def test_given_hook_succeeds_when_serve_then_proceeds_to_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Hook runs successfully; serve proceeds to PID file write + loop."""
    import subprocess

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

    def fake_run(*_args, **_kwargs):
        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    class FakeArgs:
        config = cfg_path
        once = True

    rc = serve_cmd.cmd(FakeArgs())
    assert rc == 0
    assert called["count"] == 1


def test_given_hook_raises_when_serve_then_abort_exit_1_emit_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Hook raises; serve aborts with exit 1 and emits serve_startup_hook_failed."""
    import json
    import subprocess

    from agent_runner import hooks
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

    class FakeArgs:
        config = cfg_path
        once = False

    rc = serve_cmd.cmd(FakeArgs())
    assert rc == 1
    assert called["subprocess_run"] == 0

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "expected event file to exist"
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [p for p in payloads if p["event"] == "serve_startup_hook_failed"]
    assert len(failed) == 1
    assert failed[0]["hook"] == "bad_hook"
    assert failed[0]["exc_type"] == "RuntimeError"
    assert "disk full" in failed[0]["exc_msg"]


def test_given_first_hook_raises_when_serve_then_second_hook_not_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Hooks run sequentially; first raise short-circuits subsequent hooks."""
    import subprocess

    from agent_runner import hooks
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

    class FakeArgs:
        config = cfg_path
        once = False

    rc = serve_cmd.cmd(FakeArgs())
    assert rc == 1
    assert called_b["count"] == 0
