"""Plugin hook surface for agent-runner.

Two Protocol-typed extension points loaded via setuptools entry_points at
package import:
  * PostRoundHook   — runs after agent exits, after the round_end event
  * SpawnHook       — runs at the serve admission gate, before a round spawns;
                      a read-only view of the resolved spawn (argv + env NAMES)
                      that may return proceed / defer / skip

Each hook's failure is contained: runner wraps every call in try/except and
emits a built-in ``hook_failed`` event with truncated traceback. A broken
plugin must never crash the supervisor.

Dirty-tree resolution (stash / ignore / auto_commit) is plain core, not a
plugin extension point — see ``agent_runner.vcs_state.resolve_dirty_tree``.

Public API:
  * HookContext                — narrowed runtime context passed to all hooks
  * PostRoundHook / SpawnHook — Protocols
  * SpawnView / SpawnDecision   — the read-only spawn view + a SpawnHook's verdict
  * register_post_round_hook / register_spawn_hook
  * post_round_hooks() / spawn_hooks()
  * collapse_spawn_decisions()  — pure fold of SpawnHook verdicts
                                  (skip > defer > proceed)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runner._redact import redact_secrets
from agent_runner._registry import ensure_unique
from agent_runner.api_types import SpawnDecision

_HEAD_BYTES = 1024
_TAIL_BYTES = 1024
_TRUNC_MARKER = "\n... [truncated] ...\n"
_MAX_ERROR_MSG_BYTES = 1024


def _cap_redacted(text: str, n: int) -> str:
    """Redact secrets FIRST, then cap to ``n`` chars (head + marker + tail). Redacting
    before truncating is load-bearing: a secret straddling the cut would otherwise be
    split so the anchored regexes miss it and a fragment reaches events.jsonl."""
    redacted = redact_secrets(text)
    if len(redacted) <= n:
        return redacted
    head = n // 2
    tail = n - head
    return redacted[:head] + _TRUNC_MARKER + redacted[-tail:]


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
    ``[agent] name = "acme_dev"`` does not suppress events.
    Populated by the supervisor; defaults to ``None`` for manually-constructed
    HookContext instances (rare; tests set it via ``make_hook_context``).
    """
    agent_log_path: Path | None = None
    """Path to the agent's round log (e.g.
    ``log_dir/rounds/R<N>-<timestamp>.log``); plugins parse it to extract
    usage / classify errors. Contract: the file is the agent's MERGED
    stdout+stderr — the merge is deliberate (oauth_fail / network_fail
    detection regex-scans stderr text out of it), so JSONL consumers must
    skip non-JSON lines (per-line ``json.loads`` in try/except).
    Default ``None`` for backward compatibility with manually-constructed
    HookContext instances (rare; the supervisor always populates this).
    """
    dry_run: bool = False
    """When True, plugins should skip side-effect actions (e.g. git ops,
    network calls, external state mutations). Built-in plugins ignore this
    flag (no side effects). Plugin authors read ``ctx.dry_run`` before any
    irreversible action. Populated from ``[runtime] dry_run``.
    """
    anomaly_repetitive_window: int = 0
    """Sliding-window size for repetitive-tool detection (0 = disabled).
    Populated from ``[monitor] anomaly_repetitive_window``.
    """
    anomaly_repetitive_threshold: int = 0
    """Count threshold for repetitive-tool anomaly (0 = disabled).
    Populated from ``[monitor] anomaly_repetitive_threshold``.
    """
    vcs: VcsHookView | None = None
    """Narrowed vcs config slice populated by the runner on every HookContext.
    May be None only when a HookContext is constructed without it (e.g. tests).
    """


@runtime_checkable
class PostRoundHook(Protocol):
    """Runs after agent exits, after the ``round_end`` event is emitted."""

    name: str

    def after_round(self, ctx: HookContext, result: Any) -> None: ...

    # ``result`` is ``api_types.RoundResult``; declared ``Any`` here to
    # avoid a circular import (api_types itself does not import hooks).


@runtime_checkable
class SpawnHook(Protocol):
    """Runs immediately before a round would spawn. Read-only view of the
    resolved spawn — cannot mutate argv/env. Return None ⇒ proceed."""

    name: str

    def before_spawn(self, ctx: HookContext, view: SpawnView) -> SpawnDecision | None: ...


@dataclass(frozen=True)
class SpawnView:
    """Every value in ``env`` is the empty string — a hook may check which
    names are set but can never read a secret's value through this view."""

    argv: tuple[str, ...]
    env: Mapping[str, str]


_POST_ROUND_HOOKS: list[PostRoundHook] = []
_SPAWN_HOOKS: list[SpawnHook] = []
_SPAWN_HOOK_OWNER: dict[int, str] = {}  # id(hook) -> manifest .name; "" = unknown/legacy
_SPAWN_HOOK_BUILTIN: dict[int, bool] = {}  # id(hook) -> genuine-builtin trust; absent = False
_SPAWN_HOOK_MODULE: dict[int, tuple[str, str]] = {}  # id(hook) -> (module_path, attr_path)


def register_post_round_hook(hook: PostRoundHook) -> None:
    ensure_unique(hook.name, _POST_ROUND_HOOKS, "post_round_hook")
    _POST_ROUND_HOOKS.append(hook)


def post_round_hooks() -> list[PostRoundHook]:
    return list(_POST_ROUND_HOOKS)


def register_spawn_hook(
    hook: SpawnHook,
    *,
    owner: str = "",
    builtin: bool = False,
    module_path: str = "",
    attr_path: str = "",
) -> None:
    """``builtin`` grants in-process (non-trampolined) dispatch trust, keyed on
    ``id(hook)`` — the hook OBJECT, never the collidable ``owner`` name.
    Defaults to False (fail-closed): a hook registered without it is confined
    as third-party. No first-party plugin declares ``spawn_hooks`` today, so
    every spawn hook is third-party and trampolined; the flag keeps the trust
    provenance-keyed and future-proof rather than name-based.

    ``module_path``/``attr_path`` are the DISCOVERED entry point's resolvable
    location (``module:attr``), threaded here so the trampoline re-imports the
    plugin from that path rather than from the collidable ``owner`` name — an
    entry-point name that differs from ``manifest.name`` (legal for third-party
    plugins) then still resolves."""
    ensure_unique(hook.name, _SPAWN_HOOKS, "spawn_hook")
    _SPAWN_HOOKS.append(hook)
    _SPAWN_HOOK_OWNER[id(hook)] = owner
    _SPAWN_HOOK_BUILTIN[id(hook)] = builtin
    _SPAWN_HOOK_MODULE[id(hook)] = (module_path, attr_path)


def spawn_hooks() -> list[SpawnHook]:
    return list(_SPAWN_HOOKS)


def collapse_spawn_decisions(named: list[tuple[str, SpawnDecision]]) -> SpawnDecision:
    """Pure, subprocess-free. skip > defer(max defer_s) > proceed; ties broken
    by ``named``'s own order (== registration/manifest-load order)."""
    skips = [d for _, d in named if d.action == "skip"]
    if skips:
        return skips[0]
    defers = [d for _, d in named if d.action == "defer"]
    if defers:
        return max(defers, key=lambda d: d.defer_s)
    return SpawnDecision(action="proceed")


def _summarize_error(exc: BaseException, tb: str) -> dict[str, str]:
    """Pack exception details for a ``hook_failed`` payload. Both fields redact before
    capping (see :func:`_cap_redacted`) so no secret survives the JSONL cut."""
    return {
        "error_type": type(exc).__name__,
        "error_message": _cap_redacted(str(exc), _MAX_ERROR_MSG_BYTES),
        "traceback": _cap_redacted(tb, _HEAD_BYTES + _TAIL_BYTES),
    }
