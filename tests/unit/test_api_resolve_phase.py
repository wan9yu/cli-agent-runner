"""Tests for api.resolve_runtime_for_phase public helper."""

from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_toml_with_sections


def _cfg_with_overrides(tmp_path: Path) -> Path:
    return make_toml_with_sections(
        tmp_path,
        runtime_extra="round_timeout_s = 1800\n",
        phases_block=(
            "[phases]\n"
            'list = ["dev", "qa"]\n'
            "[phases.dev]\n"
            "round_timeout_s = 3600\n"
            "disable_pre_round_hooks = true\n"
        ),
    )


def test_given_none_phase_when_resolved_then_base_runtime(tmp_path: Path) -> None:
    """resolve_runtime_for_phase(cfg, None) → base RuntimeConfig unchanged."""
    from agent_runner.api import resolve_runtime_for_phase
    from agent_runner.config import load_config

    cfg = load_config(_cfg_with_overrides(tmp_path))
    resolved = resolve_runtime_for_phase(cfg, None)
    assert resolved.round_timeout_s == 1800
    assert resolved.disable_pre_round_hooks is False


def test_given_phase_with_override_when_resolved_then_override_applied(tmp_path: Path) -> None:
    """resolve_runtime_for_phase(cfg, 'dev') → timeout=3600, disable_pre_round_hooks=True."""
    from agent_runner.api import resolve_runtime_for_phase
    from agent_runner.config import load_config

    cfg = load_config(_cfg_with_overrides(tmp_path))
    resolved = resolve_runtime_for_phase(cfg, "dev")
    assert resolved.round_timeout_s == 3600
    assert resolved.disable_pre_round_hooks is True


def test_given_phase_without_override_when_resolved_then_base_returned(tmp_path: Path) -> None:
    """resolve_runtime_for_phase(cfg, 'qa') with no [phases.qa] sub-table → base unchanged."""
    from agent_runner.api import resolve_runtime_for_phase
    from agent_runner.config import load_config

    cfg = load_config(_cfg_with_overrides(tmp_path))
    resolved = resolve_runtime_for_phase(cfg, "qa")
    assert resolved.round_timeout_s == 1800
    assert resolved.disable_pre_round_hooks is False


def test_given_unknown_phase_when_resolved_then_base_returned(tmp_path: Path) -> None:
    """resolve_runtime_for_phase(cfg, 'nope') silently returns base (defensive)."""
    from agent_runner.api import resolve_runtime_for_phase
    from agent_runner.config import load_config

    cfg = load_config(_cfg_with_overrides(tmp_path))
    resolved = resolve_runtime_for_phase(cfg, "nope")
    assert resolved.round_timeout_s == 1800
