"""Plugin hook surface for agent-runner.

One Protocol-typed extension point loaded via setuptools entry_points at
package import:
  * PostRoundHook   — runs after agent exits, after the round_end event

Each hook's failure is contained: runner wraps every call in try/except and
emits a built-in ``hook_failed`` event with truncated traceback. A broken
plugin must never crash the supervisor.

Dirty-tree resolution (stash / ignore / auto_commit) is plain core, not a
plugin extension point — see ``agent_runner.vcs_state.resolve_dirty_tree``.

Public API:
  * HookContext        — narrowed runtime context passed to all hooks
  * PostRoundHook       — Protocol
  * register_post_round_hook
  * post_round_hooks()
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runner._redact import redact_secrets
from agent_runner._registry import ensure_unique

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


_POST_ROUND_HOOKS: list[PostRoundHook] = []


def register_post_round_hook(hook: PostRoundHook) -> None:
    ensure_unique(hook.name, _POST_ROUND_HOOKS, "post_round_hook")
    _POST_ROUND_HOOKS.append(hook)


def post_round_hooks() -> list[PostRoundHook]:
    return list(_POST_ROUND_HOOKS)


def _summarize_error(exc: BaseException, tb: str) -> dict[str, str]:
    """Pack exception details for a ``hook_failed`` payload. Both fields redact before
    capping (see :func:`_cap_redacted`) so no secret survives the JSONL cut."""
    return {
        "error_type": type(exc).__name__,
        "error_message": _cap_redacted(str(exc), _MAX_ERROR_MSG_BYTES),
        "traceback": _cap_redacted(tb, _HEAD_BYTES + _TAIL_BYTES),
    }
