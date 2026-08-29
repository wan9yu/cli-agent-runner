"""Invariant: phase_select.select_phase is stateless.

Its result must be a pure function of ``(cfg, round_num, now, throttled_phases)``
— it must NOT read status.json, event history, or any on-disk supervisor state.
The throttled set is INJECTED by the serve layer (like ``now_fn``); reading
throttle state inside the module would break statelessness. If a future edit
reaches for round-history to decide the phase, the selection stops being
reconstructible from the config + clock + injected args alone (which is what
makes serve and ``round --phase`` agree), so this guards the source structurally.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "agent_runner" / "phase_select.py"

# Names that would signal reading supervisor run-state inside the module.
_FORBIDDEN_STATE = {
    "context_store",
    "read_status",
    "load_status",
    "next_round_num",
    "Status",
    "round_log",
    "narrate_events",
    "stream_events_jsonl",
    "read_events",
}


def test_select_phase_reads_no_supervisor_state() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_STATE:
            hits.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_STATE:
            hits.add(node.attr)
    assert not hits, f"phase_select must stay stateless; references state: {sorted(hits)}"


def test_select_phase_signature_is_cfg_round_clock() -> None:
    """The public entry takes exactly (cfg, round_num, *, throttled_phases, now_fn)
    — no log_dir / status handle through which run-state could leak in. The
    throttled set is a value injected by the caller, not read from disk."""
    import inspect

    from agent_runner import phase_select

    params = list(inspect.signature(phase_select.select_phase).parameters)
    assert params == ["cfg", "round_num", "throttled_phases", "now_fn"], params
