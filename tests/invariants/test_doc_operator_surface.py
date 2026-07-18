"""Invariants for the mechanizable subset of the operator-surface doc sweep.

Items 7/14/24/25/26 are prose corrections verified by execution at fix time and
carry no natural guard — manufacturing one would be the ceremony this release is
right-sizing. The three below are real properties with real SSOTs.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runner.vcs_state import _PLUGIN_OWNED_PATHS
from tests._test_helpers import isolating

REPO = Path(__file__).resolve().parents[2]

_reset = isolating(_PLUGIN_OWNED_PATHS)


def test_given_configuration_doc_when_read_then_phase_formula_matches_runner() -> None:
    """The [phases] table and its callout stated different formulas 8 lines apart.
    runner._phase_for is the SSOT and round_num is 1-based."""
    from agent_runner.runner import _phase_for

    phases = ["dev", "qa", "product"]
    for n in range(1, 10):
        assert _phase_for(n, phases)[0] == phases[(n - 1) % len(phases)], (
            "runner._phase_for no longer matches the documented (N-1) % len formula"
        )
    text = (REPO / "docs/configuration.md").read_text(encoding="utf-8")
    assert "round_num % len" not in text, (
        "configuration.md still states the 0-based formula; round_num is 1-based "
        "(runner.py:408), so rotation is phases[(round_num - 1) % len]"
    )


def test_given_documented_owned_path_patterns_when_matched_then_table_is_true() -> None:
    """Every row of docs/plugins.md's plugin-owned-paths matching table, read
    FROM the doc and checked against the real matcher.

    The `reports/**/*.md` row was the one row with no test, and it was false:
    fnmatch's `**/` required at least one intervening directory segment. The
    globstar matcher makes `**/` mean zero-or-more segments, honoring the row.
    """
    from agent_runner.vcs_state import _matches_owned_path, register_plugin_owned_paths

    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    section = text.split("### Matching semantics", 1)[-1].split("\n### ", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
    assert len(rows) == 5, f"table shape changed ({len(rows)} rows) — update this guard"

    failures: list[str] = []
    for row in rows:
        cols = [c.strip() for c in row.strip().strip("|").split("|")]
        pattern = re.search(r'`"([^"]+)"`', cols[0]).group(1)
        documented_hits = re.findall(r"`([^`]+)`", cols[1])
        _PLUGIN_OWNED_PATHS.clear()
        register_plugin_owned_paths([pattern])
        for hit in documented_hits:
            if not _matches_owned_path(hit):
                failures.append(f"docs claim {pattern!r} matches {hit!r}; it does not")
    assert not failures, "plugin-owned-paths table drift:\n" + "\n".join(failures)


def test_given_architecture_doc_when_read_then_no_false_flag_symmetry_claim() -> None:
    """architecture.md claimed peek/watch/monitor share drill-down flags.
    monitor's parser has none of them; argparse exits 2."""
    from agent_runner.cli import _build_parser

    parser = _build_parser()
    subs = [a for a in parser._subparsers._group_actions if hasattr(a, "choices")]
    choices = {c: p for a in subs for c, p in a.choices.items()}
    monitor_flags = {opt for act in choices["monitor"]._actions for opt in act.option_strings}
    drill_down = {"--round", "--log", "--events", "--select"}
    assert not (drill_down & monitor_flags), (
        "monitor gained drill-down flags — architecture.md:32's claim may now be true"
    )
    text = (REPO / "docs/architecture.md").read_text(encoding="utf-8")
    assert "All three accept the same drill-down flags" not in text, (
        "architecture.md still claims monitor accepts peek's drill-down flags; "
        f"monitor accepts only {sorted(monitor_flags)}"
    )
