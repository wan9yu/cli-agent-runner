"""Plugin hook surface for agent-runner.

Four Protocol-typed extension points loaded via setuptools entry_points at
package import:
  * PreRoundHook    — runs after lock acquired, before context is written
  * ContextEnricher — returns a per-plugin slice merged into round-context.json
                      under base_context[enricher.name] (namespacing prevents
                      collisions structurally)
  * PostRoundHook   — runs after agent exits, before round_end event
  * ServeStartupHook — fires once per ``agent-runner serve`` boot, before the
                       round loop; receives the loaded Config

Each hook's failure is contained: runner wraps every call in try/except and
emits a built-in ``hook_failed`` event with truncated traceback. A broken
plugin must never crash the supervisor.

Public API:
  * HookContext                — narrowed runtime context passed to all hooks
  * PreRoundHook / ContextEnricher / PostRoundHook / ServeStartupHook — Protocols
  * register_pre_round_hook / register_context_enricher / register_post_round_hook
    / register_serve_startup_hook
  * pre_round_hooks() / context_enrichers() / post_round_hooks()
    / serve_startup_hooks()
  * run_serve_startup_hooks()   — orchestrates serve-startup hook execution with
                                  structured stderr + best-effort event emission
  * plugin_context_enrichers()  — sorted list of registered enricher names
                                  (used by ``peek --json`` plugins namespace)
"""

from __future__ import annotations

import sys
import traceback as tb_mod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runner import events
from agent_runner._redact import redact_secrets
from agent_runner._registry import ensure_unique

_HEAD_BYTES = 1024
_TAIL_BYTES = 1024
_TRUNC_MARKER = "\n... [truncated] ...\n"


@dataclass(frozen=True)
class VcsHookView:
    """Minimal vcs config a dirty handler needs (keeps HookContext narrow)."""

    dirty_action: str
    stash_idempotency_s: int


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
    agent_binary: str | None = None
    """Basename of ``cfg.agent.command[0]`` (e.g. ``'claude'``, ``'gemini'``).
    Distinct from ``agent_name`` (the user-cosmetic ``[agent] name`` field).
    Plugins should guard on ``agent_binary``, not ``agent_name``, so a custom
    ``[agent] name = "argus_dev"`` does not suppress events.
    Populated from 0.1.30+; defaults to ``None`` for manually-constructed
    HookContext instances (rare; tests set it via ``make_hook_context``).
    """
    agent_log_path: Path | None = None
    """Path to the agent's stdout JSONL log for this round (e.g.
    ``log_dir/rounds/R<N>-<timestamp>.log``). Plugins parse this to
    extract usage / classify errors. Default ``None`` for backward
    compatibility with manually-constructed HookContext instances
    (rare; the supervisor always populates this from 0.1.25+).
    """
    dry_run: bool = False
    """When True, plugins should skip side-effect actions (e.g. git ops,
    network calls, external state mutations). Built-in plugins ignore this
    flag (no side effects). Plugin authors read ``ctx.dry_run`` before any
    irreversible action. Populated from ``[runtime] dry_run`` (0.1.31+).
    """
    anomaly_repetitive_window: int = 0
    """Sliding-window size for repetitive-tool detection (0 = disabled).
    Populated from ``[monitor] anomaly_repetitive_window`` (0.1.31+).
    """
    anomaly_repetitive_threshold: int = 0
    """Count threshold for repetitive-tool anomaly (0 = disabled).
    Populated from ``[monitor] anomaly_repetitive_threshold`` (0.1.31+).
    """
    vcs: VcsHookView | None = None
    """Narrowed vcs config slice populated by the runner for all per-round hooks
    (pre-round, enrichers, post-round, dirty-handler) (0.2.0+).
    May be None only when a HookContext is constructed without it (e.g. tests).
    """


@runtime_checkable
class DirtyHandler(Protocol):
    """Resolves a clean-exit dirty tree. Runs ascending by ``priority``; the
    first to return a DirtyOutcome wins. Return None to pass to the next."""

    name: str
    priority: int

    def handle_dirty(
        self,
        ctx: HookContext,
        dirty_files: list[str],
    ) -> Any | None: ...


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


@runtime_checkable
class ServeStartupHook(Protocol):
    """Fires once per ``agent-runner serve`` boot, before the round loop.

    Receives the loaded ``Config``; returns nothing. Used by plugins to seed
    state that subsequent rounds depend on (e.g. a default prompt file).

    Failure semantics: if a hook raises, ``agent-runner serve`` aborts with
    exit code 1 and emits ``serve_startup_hook_failed`` (best-effort). Hooks
    are plugin contracts — failing fast beats mysterious per-round errors.
    """

    name: str

    def __call__(self, cfg: Any) -> None: ...


_PRE_ROUND_HOOKS: list[PreRoundHook] = []
_CONTEXT_ENRICHERS: list[ContextEnricher] = []
_POST_ROUND_HOOKS: list[PostRoundHook] = []
_SERVE_STARTUP_HOOKS: list[ServeStartupHook] = []
_DIRTY_HANDLERS: list[DirtyHandler] = []


def register_pre_round_hook(hook: PreRoundHook) -> None:
    ensure_unique(hook.name, _PRE_ROUND_HOOKS, "pre_round_hook")
    _PRE_ROUND_HOOKS.append(hook)


def register_context_enricher(enricher: ContextEnricher) -> None:
    ensure_unique(enricher.name, _CONTEXT_ENRICHERS, "context_enricher")
    _CONTEXT_ENRICHERS.append(enricher)


def register_post_round_hook(hook: PostRoundHook) -> None:
    ensure_unique(hook.name, _POST_ROUND_HOOKS, "post_round_hook")
    _POST_ROUND_HOOKS.append(hook)


def register_serve_startup_hook(hook: ServeStartupHook) -> None:
    ensure_unique(hook.name, _SERVE_STARTUP_HOOKS, "serve_startup_hook")
    _SERVE_STARTUP_HOOKS.append(hook)


def pre_round_hooks() -> list[PreRoundHook]:
    return list(_PRE_ROUND_HOOKS)


def context_enrichers() -> list[ContextEnricher]:
    return list(_CONTEXT_ENRICHERS)


def post_round_hooks() -> list[PostRoundHook]:
    return list(_POST_ROUND_HOOKS)


def serve_startup_hooks() -> list[ServeStartupHook]:
    return list(_SERVE_STARTUP_HOOKS)


def plugin_context_enrichers() -> list[str]:
    """Sorted list of registered enricher names — used by peek --json."""
    return sorted(e.name for e in _CONTEXT_ENRICHERS)


def register_dirty_handler(handler: DirtyHandler) -> None:
    ensure_unique(handler.name, _DIRTY_HANDLERS, "dirty_handler")
    _DIRTY_HANDLERS.append(handler)


def dirty_handlers() -> list[DirtyHandler]:
    return list(_DIRTY_HANDLERS)


def dispatch_dirty(
    ctx: HookContext,
    dirty_files: list[str],
    log_dir: Path,
) -> Any | None:
    """Run registered DirtyHandlers ascending by priority; first non-None wins.

    A handler that raises is isolated — emits ``hook_failed`` with
    ``hook_kind="dirty_handler"`` and treated as pass (continue to next).
    """
    ordered = sorted(_DIRTY_HANDLERS, key=lambda h: getattr(h, "priority", 0))
    for h in ordered:
        try:
            outcome = h.handle_dirty(ctx, dirty_files)
        except Exception as exc:  # noqa: BLE001 — isolate; fall through to next
            events.emit(
                log_dir,
                events.HOOK_FAILED,
                hook_name=h.name,
                hook_kind="dirty_handler",
                **_summarize_error(exc, tb=tb_mod.format_exc()),
            )
            continue
        if outcome is not None:
            return outcome
    return None


def run_serve_startup_hooks(cfg: Any, log_dir: Path) -> bool:
    """Run all serve_startup_hooks. Returns True on success, False on abort.

    On first hook failure: print structured stderr, best-effort emit
    ``serve_startup_hook_failed`` event, return False (caller exits 1).
    """
    for hook in serve_startup_hooks():
        try:
            hook(cfg)
        except Exception as e:  # noqa: BLE001 — hook is plugin contract; any failure aborts serve
            exc_type = type(e).__name__
            exc_msg = redact_secrets(str(e))[:200]
            print(
                f"agent-runner: serve_startup_hook {hook.name} failed: {exc_type}: {exc_msg}",
                file=sys.stderr,
            )
            try:
                events.emit(
                    log_dir,
                    events.SERVE_STARTUP_HOOK_FAILED,
                    hook=hook.name,
                    exc_type=exc_type,
                    exc_msg=exc_msg,
                )
            except Exception:  # noqa: BLE001 — best-effort emit; log_dir may be unwritable
                pass
            return False
    return True


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
        "error_message": redact_secrets(str(exc)),
        "traceback": redact_secrets(trimmed),
    }
