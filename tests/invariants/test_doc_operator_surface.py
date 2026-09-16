"""Invariants for the mechanizable subset of the operator-surface doc sweep.

Items 7/14/24/25/26 are prose corrections verified by execution at fix time and
carry no natural guard — manufacturing one would be the ceremony this release is
right-sizing. The two below are real properties with real SSOTs.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_configuration_doc_should_match_runner_phase_formula_when_read() -> None:
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


def test_architecture_doc_should_not_claim_flag_symmetry_when_read() -> None:
    """architecture.md AND commands.md claimed peek/watch/monitor share drill-down
    flags. monitor's parser has none of them; argparse exits 2."""
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

    cmds = (REPO / "docs/commands.md").read_text(encoding="utf-8")
    assert "shared between `peek` and `watch`" in cmds, (
        "commands.md no longer scopes drill-down flags to peek and watch"
    )
    assert "monitor 不接受这些下钻参数" in cmds, (
        "commands.md's 中文摘要 no longer excludes monitor from the drill-down flags"
    )
    assert "三视角对称，全部共用" not in cmds, (
        "commands.md revived the false claim that all observation verbs share the "
        f"drill-down flags; monitor accepts only {sorted(monitor_flags)}"
    )
