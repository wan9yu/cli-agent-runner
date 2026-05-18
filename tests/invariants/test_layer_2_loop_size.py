"""Layer 2 (serve loop) LOC invariant.

README claims: 'Layer 2: The Loop (serve, ~60 LOC) — signal-trapping
restart loop'. The supervisor loop is ``serve_cmd.cmd``; it currently
runs ~120 LOC (non-blank, non-comment lines). The README "~60 LOC" claim
predates several feature additions. This invariant guards against further
unchecked growth.

If you're tempted to "just add X to the supervisor loop", extract X into:
- a defense (events.py + monitor.py)
- a hook (PreRoundHook / PostRoundHook plugin)
- a helper (separate function or module)

Bump ``SERVE_LOOP_BUDGET`` only if a design decision warrants it (and
document why in the commit message).
"""

from __future__ import annotations

import inspect

SERVE_LOOP_BUDGET = 140  # current ~120 LOC + 20 headroom; tighten over time


def test_serve_loop_stays_minimal():
    from agent_runner.cli import serve_cmd

    func = serve_cmd.cmd
    src = inspect.getsource(func)
    lines = src.splitlines()
    loc = sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))
    assert loc <= SERVE_LOOP_BUDGET, (
        f"serve_cmd.cmd is {loc} LOC (non-blank, non-comment), budget {SERVE_LOOP_BUDGET}. "
        f"Extract new logic into defenses, hooks, or helpers. "
        f"Bump SERVE_LOOP_BUDGET only if a design decision warrants it."
    )
