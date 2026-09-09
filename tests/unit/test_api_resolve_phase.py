"""Tests for api.resolve_runtime_for_phase public helper."""

from __future__ import annotations

from pathlib import Path

from agent_runner.api import resolve_runtime_for_phase
from agent_runner.config import load_config
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


def test_resolve_runtime_for_phase_should_return_base_runtime_when_phase_is_none(
    tmp_path: Path,
) -> None:
    cfg = load_config(_cfg_with_overrides(tmp_path))

    resolved = resolve_runtime_for_phase(cfg, None)

    assert resolved.round_timeout_s == 1800
    assert resolved.disable_pre_round_hooks is False


def test_resolve_runtime_for_phase_should_apply_override_when_phase_has_override(
    tmp_path: Path,
) -> None:
    cfg = load_config(_cfg_with_overrides(tmp_path))

    resolved = resolve_runtime_for_phase(cfg, "dev")

    assert resolved.round_timeout_s == 3600
    assert resolved.disable_pre_round_hooks is True


def test_resolve_runtime_for_phase_should_return_base_when_phase_has_no_override(
    tmp_path: Path,
) -> None:
    cfg = load_config(_cfg_with_overrides(tmp_path))

    resolved = resolve_runtime_for_phase(cfg, "qa")

    assert resolved.round_timeout_s == 1800
    assert resolved.disable_pre_round_hooks is False


def test_resolve_runtime_for_phase_should_return_base_when_phase_is_unknown(
    tmp_path: Path,
) -> None:
    cfg = load_config(_cfg_with_overrides(tmp_path))

    resolved = resolve_runtime_for_phase(cfg, "nope")

    assert resolved.round_timeout_s == 1800
