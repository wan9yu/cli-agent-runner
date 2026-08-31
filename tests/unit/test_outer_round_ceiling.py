"""B2: the outer round ceiling is a derived budget (inner round_timeout + reap
grace + git-commit ceiling + hook allowance), resolving per-phase timeouts."""

from __future__ import annotations

from pathlib import Path

from agent_runner import api
from agent_runner.agent_runtime import REAP_GRACE_S
from agent_runner.config import load_config
from agent_runner.vcs_state import GIT_COMMIT_TIMEOUT_S


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
    margin = REAP_GRACE_S + GIT_COMMIT_TIMEOUT_S + api._HOOK_ALLOWANCE_S
    assert api.outer_round_ceiling_s(cfg, None) == 100 + margin


def test_ceiling_uses_selected_phase_timeout(tmp_path):
    cfg = _cfg(
        tmp_path,
        "[phases]\nlist = ['fast', 'slow']\n[phases.slow.runtime]\nround_timeout_s = 900\n",
    )
    margin = REAP_GRACE_S + GIT_COMMIT_TIMEOUT_S + api._HOOK_ALLOWANCE_S
    assert api.outer_round_ceiling_s(cfg, "slow") == 900 + margin
    # rotation (phase_arg None) must not under-budget a phase that overrides larger
    assert api.outer_round_ceiling_s(cfg, None) == 900 + margin
