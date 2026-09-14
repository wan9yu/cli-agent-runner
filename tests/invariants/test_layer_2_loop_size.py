"""Layer 2 (serve loop) LOC invariant.

README claims: 'Layer 2: The Loop (serve, ~120 LOC) — signal-trapping
restart loop'. The supervisor loop is ``serve_cmd.cmd``; it currently
runs ~120 LOC (non-blank, non-comment lines). This invariant guards
against further unchecked growth.

If you're tempted to "just add X to the supervisor loop", extract X into:
- a defense (events.py + monitor.py)
- a hook (PreRoundHook / PostRoundHook plugin)
- a helper (separate function or module)

Bump ``SERVE_LOOP_BUDGET`` only if a design decision warrants it (and
document why in the commit message).
"""

from __future__ import annotations

import inspect

SERVE_LOOP_BUDGET = 145  # cmd() wires the process-lifetime doorbell listener into
# every serve wait -- mid-round, both pauses, phase-select, and the restart
# delay -- so a SIGTERM/ring wakes each near-instantly instead of riding out a
# 30s chunk (0.3.4 event-driven-core, Component 5); current 143 LOC + 2 headroom


def test_serve_loop_should_stay_minimal():
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
