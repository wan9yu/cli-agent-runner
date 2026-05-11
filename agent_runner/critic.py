"""Phase 3 critic interface — empty stub in Phase 2.

A Critic analyses the current ProjectState (recent rounds, defenses, events)
and emits Findings: drift / dark-code / inefficiency observations that should
be fed into the next round's prompt context or recorded for the operator.

Phase 3 implements concrete Critics (LLM-backed, invariant-runner, etc.).
Phase 2 ships only the Protocols so the rest of the system can reference
the type without committing to an implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runner.api_types import ProjectState


@runtime_checkable
class Finding(Protocol):
    """A single observation emitted by a Critic."""

    severity: str  # "info" | "warning" | "critical"
    detector: str  # critic-defined identifier
    message: str
    suggested_action: str | None


@runtime_checkable
class Critic(Protocol):
    """Phase 3 implements: analyse a ProjectState snapshot, return findings."""

    def analyze(self, state: ProjectState) -> list[Finding]: ...
