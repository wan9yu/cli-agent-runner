"""Tests for serve_startup_hooks registry + Protocol."""

from __future__ import annotations

import pytest


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
