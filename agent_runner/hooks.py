"""Plugin hook surface for agent-runner.

Three Protocol-typed extension points loaded via setuptools entry_points at
package import:
  * PreRoundHook   — runs after lock acquired, before context is written
  * ContextEnricher — returns a per-plugin slice merged into round-context.json
                      under base_context[enricher.name] (namespacing prevents
                      collisions structurally)
  * PostRoundHook  — runs after agent exits, before round_end event

Each hook's failure is contained: runner wraps every call in try/except and
emits a built-in ``hook_failed`` event with truncated traceback. A broken
plugin must never crash the supervisor.

Public API:
  * HookContext                — narrowed runtime context passed to all hooks
  * PreRoundHook / ContextEnricher / PostRoundHook  — Protocols
  * register_pre_round_hook / register_context_enricher / register_post_round_hook
  * pre_round_hooks() / context_enrichers() / post_round_hooks()
  * plugin_context_enrichers()  — sorted list of registered enricher names
                                  (used by ``peek --json`` plugins namespace)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runner._registry import ensure_unique

_HEAD_BYTES = 1024
_TAIL_BYTES = 1024
_TRUNC_MARKER = "\n... [truncated] ...\n"


@dataclass(frozen=True)
class HookContext:
    """Narrow per-round context passed to all hook callbacks.

    Intentionally smaller than ``Config`` — exposes only what plugins
    legitimately need so internal config refactors stay safe.
    """

    work_dir: Path
    log_dir: Path
    project: str
    round_num: int
    phase: str | None
    agent_name: str | None


@runtime_checkable
class PreRoundHook(Protocol):
    """Runs after lock acquisition, before round-context is written.

    Side-effectful — intended for cache refresh, external state snapshots,
    etc. Return value (if any) is ignored.
    """

    name: str

    def before_round(self, ctx: HookContext) -> None: ...


@runtime_checkable
class ContextEnricher(Protocol):
    """Returns this plugin's slice of round context.

    The runner places the return value at ``base_context[enricher.name]`` —
    no collision is possible because each enricher's slot is keyed by its
    own ``name``.
    """

    name: str

    def enrich(self, ctx: HookContext) -> dict[str, Any]: ...


@runtime_checkable
class PostRoundHook(Protocol):
    """Runs after agent exits, before ``round_end`` event is emitted."""

    name: str

    def after_round(self, ctx: HookContext, result: Any) -> None: ...

    # ``result`` is ``api_types.RoundResult``; declared ``Any`` here to
    # avoid a circular import (api_types itself does not import hooks).


_PRE_ROUND_HOOKS: list[PreRoundHook] = []
_CONTEXT_ENRICHERS: list[ContextEnricher] = []
_POST_ROUND_HOOKS: list[PostRoundHook] = []


def register_pre_round_hook(hook: PreRoundHook) -> None:
    ensure_unique(hook.name, _PRE_ROUND_HOOKS, "pre_round_hook")
    _PRE_ROUND_HOOKS.append(hook)


def register_context_enricher(enricher: ContextEnricher) -> None:
    ensure_unique(enricher.name, _CONTEXT_ENRICHERS, "context_enricher")
    _CONTEXT_ENRICHERS.append(enricher)


def register_post_round_hook(hook: PostRoundHook) -> None:
    ensure_unique(hook.name, _POST_ROUND_HOOKS, "post_round_hook")
    _POST_ROUND_HOOKS.append(hook)


def pre_round_hooks() -> list[PreRoundHook]:
    return list(_PRE_ROUND_HOOKS)


def context_enrichers() -> list[ContextEnricher]:
    return list(_CONTEXT_ENRICHERS)


def post_round_hooks() -> list[PostRoundHook]:
    return list(_POST_ROUND_HOOKS)


def plugin_context_enrichers() -> list[str]:
    """Sorted list of registered enricher names — used by peek --json."""
    return sorted(e.name for e in _CONTEXT_ENRICHERS)


def _summarize_error(exc: BaseException, tb: str) -> dict[str, str]:
    """Pack exception details for a ``hook_failed`` event payload.

    Truncates ``tb`` to ``_HEAD_BYTES`` + ``_TAIL_BYTES`` with a separator
    so the JSONL stream doesn't bloat from one runaway hook.
    """
    if len(tb) <= _HEAD_BYTES + _TAIL_BYTES:
        trimmed = tb
    else:
        trimmed = tb[:_HEAD_BYTES] + _TRUNC_MARKER + tb[-_TAIL_BYTES:]
    return {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": trimmed,
    }
