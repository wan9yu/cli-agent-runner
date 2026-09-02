"""B2: the outer round ceiling is a derived budget (inner round_timeout + reap
grace + git-commit ceiling + hook allowance), resolving per-phase timeouts.

Group C (0.2.13): the margin is single-sourced via
``_serve_policy.timeout_budget`` (shared with service_unit.py's
TimeoutStopSec) — see test_timeout_budget_invariant.py for the budget's own
invariants; this file only pins that api.outer_round_ceiling_s uses it
correctly, resolving per-phase timeouts."""

from __future__ import annotations

from pathlib import Path

from agent_runner import _serve_policy, api
from agent_runner.config import load_config


def _cfg(tmp_path: Path, extra: str = "") -> api.Config:
    (tmp_path / "prompt.md").write_text("p")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\ncommand = ['true']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = './logs'\nround_timeout_s = 100\n"
        "[prompt]\nfile = 'prompt.md'\n" + extra
    )
    return load_config(toml)


def test_ceiling_adds_derived_margin_for_base_timeout(tmp_path):
    cfg = _cfg(tmp_path)
    _, expected_ceiling = _serve_policy.timeout_budget(100)
    assert api.outer_round_ceiling_s(cfg, None) == expected_ceiling


def test_ceiling_uses_selected_phase_timeout(tmp_path):
    cfg = _cfg(
        tmp_path,
        "[phases]\nlist = ['fast', 'slow']\n[phases.slow.runtime]\nround_timeout_s = 900\n",
    )
    _, expected_ceiling = _serve_policy.timeout_budget(900)
    assert api.outer_round_ceiling_s(cfg, "slow") == expected_ceiling
    # rotation (phase_arg None) must not under-budget a phase that overrides larger
    assert api.outer_round_ceiling_s(cfg, None) == expected_ceiling
