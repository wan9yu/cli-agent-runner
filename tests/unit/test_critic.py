from __future__ import annotations

import inspect

from agent_runner import critic


def test_given_critic_module_when_inspected_then_exposes_protocols() -> None:
    assert hasattr(critic, "Critic")
    assert hasattr(critic, "Finding")


def test_given_critic_module_when_inspected_then_has_no_concrete_implementations() -> None:
    """Phase 2 ships only Protocols; Phase 3 will add concrete Critics."""
    classes = [
        m
        for _, m in inspect.getmembers(critic, inspect.isclass)
        if m.__module__ == "agent_runner.critic"
    ]
    for cls in classes:
        is_protocol = getattr(cls, "_is_protocol", False) or getattr(
            cls, "_is_runtime_protocol", False
        )
        is_dataclass = hasattr(cls, "__dataclass_fields__")
        assert is_protocol or is_dataclass, (
            f"{cls.__name__} is neither Protocol nor dataclass — Phase 2 forbids concrete Critics"
        )
