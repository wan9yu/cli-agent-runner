"""Invariant: each _VALID_* frozenset matches its dataclass Literal annotation.

The two are independent copies of the same value set. _docgen renders the
*Literal* into docs/configuration.md's generated config-schema table, while
load_config validates against the *frozenset* — so a value added to one and not
the other ships a reference that contradicts the loader, with a green suite.

Guards all four sets uniformly; two of them (prompt_delivery,
transient_error_action) had no guard of any kind.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args, get_type_hints

from agent_runner.config import (
    _VALID_DIRTY_ACTIONS,
    _VALID_INJECTION_MODES,
    _VALID_PROMPT_DELIVERY,
    _VALID_TRANSIENT_ERROR_ACTIONS,
    AgentConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)

REPO = Path(__file__).resolve().parents[2]

# (dataclass, field name, frozenset SSOT the loader validates against)
_PAIRS = [
    (AgentConfig, "prompt_delivery", _VALID_PROMPT_DELIVERY),
    (RuntimeConfig, "transient_error_action", _VALID_TRANSIENT_ERROR_ACTIONS),
    (PromptConfig, "context_injection_mode", _VALID_INJECTION_MODES),
    (VcsConfig, "dirty_action", _VALID_DIRTY_ACTIONS),
]


def test_given_config_literals_when_compared_then_match_valid_frozensets() -> None:
    failures: list[str] = []
    for cls, field, ssot in _PAIRS:
        # get_type_hints (not raw __annotations__): `from __future__ import
        # annotations` makes every annotation a string. Never call it on
        # PhasesConfig — its `list` field shadows the builtin and it raises.
        literal = set(get_args(get_type_hints(cls)[field]))
        if literal != set(ssot):
            failures.append(f"{cls.__name__}.{field}: Literal {literal} != SSOT {set(ssot)}")
    assert not failures, "config value-set drift:\n" + "\n".join(failures)


def test_given_monitor_module_source_when_scanned_then_detector_count_matches() -> None:
    """monitor.py states its own detector count in two docstrings. Guard both —
    the doc-claims registry covers markdown only, and this number drifted to 12."""
    from agent_runner.monitor import KNOWN_ALERT_KINDS

    src = (REPO / "agent_runner/monitor.py").read_text(encoding="utf-8")
    expected = len(KNOWN_ALERT_KINDS)
    found = re.findall(r"(\d+) built-in detectors|Run all (\d+) detectors", src)
    claims = [int(a or b) for a, b in found]
    assert claims, "monitor.py no longer states a detector count (reworded? update guard)"
    assert all(c == expected for c in claims), (
        f"monitor.py claims {claims} detectors; KNOWN_ALERT_KINDS has {expected}"
    )
