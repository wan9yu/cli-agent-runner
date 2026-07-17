from __future__ import annotations

import dataclasses
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.defenses import Defense, catalog


def _cfg(tmp_path: Path, *, env: dict[str, str] | None = None) -> Config:
    return Config(
        agent=AgentConfig(
            command=["my-agent"],
            prompt_arg_template=["-p", "{prompt}"],
            env=env or {},
        ),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=tmp_path / "p.md", inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )


def test_given_defense_when_inspected_then_is_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Defense)
    assert Defense.__dataclass_params__.frozen


def test_given_catalog_when_called_then_returns_twelve_entries(tmp_path: Path) -> None:
    cat = catalog(_cfg(tmp_path))
    assert len(cat) == 12


def test_given_catalog_when_called_then_each_has_required_fields(tmp_path: Path) -> None:
    for d in catalog(_cfg(tmp_path)):
        assert d.name
        assert d.current_state in {"active", "degraded", "off"}


def test_given_round_timeout_defense_when_inspected_then_value_matches_config(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    object.__setattr__(cfg.runtime, "round_timeout_s", 999)
    cat = catalog(cfg)
    rt = next(d for d in cat if d.name == "round_timeout_s")
    assert rt.value == 999


def test_given_catalog_when_inspected_then_codified_incidents_present(tmp_path: Path) -> None:
    cat = catalog(_cfg(tmp_path))
    incident_codes = " ".join(d.codifies or "" for d in cat)
    for code in ("R1128", "R725", "R820", "§9", "R2110", "R721"):
        assert code in incident_codes, f"defense catalog missing reference to {code}"


def test_given_catalog_invariant_paths_when_resolved_then_all_exist(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    for d in catalog(_cfg(tmp_path)):
        assert d.guarded_by is not None, f"defense {d.name} names no test that guards it"
        full = repo_root / d.guarded_by
        assert full.exists(), f"defense {d.name} references missing test {d.guarded_by}"


def test_given_active_defense_when_inspected_then_has_guarded_by(tmp_path: Path) -> None:
    """A defense claiming "active" with nothing behind it is an unverified claim.

    Deliberately not folded into the unconditional check above: guarded_by stays
    ``Path | None`` for plugin-supplied entries, so that check may one day have to
    weaken. This is the floor that must survive it. Both env variants are walked
    because critical_envs_injection is the one entry whose state depends on config —
    it reads "off" under the default empty ``[agent.env]``.
    """
    for cfg in (_cfg(tmp_path), _cfg(tmp_path, env={"DISABLE_AUTOUPDATER": "1"})):
        for d in catalog(cfg):
            if d.current_state == "active":
                assert d.guarded_by is not None, (
                    f"defense {d.name} claims active but names no test that guards it"
                )


def test_given_defense_names_when_collected_then_unique(tmp_path: Path) -> None:
    names = [d.name for d in catalog(_cfg(tmp_path))]
    assert len(names) == len(set(names)), "duplicate defense names in catalog"


def test_given_cfg_with_agent_env_when_catalog_then_critical_envs_lists_keys(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, env={"DISABLE_AUTOUPDATER": "1", "FOO": "bar"})
    cat = catalog(cfg)
    row = next(d for d in cat if d.name == "critical_envs_injection")
    assert sorted(row.value) == ["DISABLE_AUTOUPDATER", "FOO"]
    assert row.current_state == "active"


def test_given_cfg_with_empty_agent_env_when_catalog_then_critical_envs_off(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, env={})
    cat = catalog(cfg)
    row = next(d for d in cat if d.name == "critical_envs_injection")
    assert row.value == []
    assert row.current_state == "off"


def test_given_sigterm_reaper_defense_when_inspected_then_names_graceful_stop(
    tmp_path: Path,
) -> None:
    """R725 is delivered by serve's graceful-stop contract, not by a signal handler."""
    row = next(d for d in catalog(_cfg(tmp_path)) if d.name == "sigterm_reaper")
    assert "install_sigterm_reaper" not in row.value
    assert row.guarded_by == Path("tests/integration/test_serve_loop.py")


def test_given_set_diff_defense_when_inspected_then_names_the_prohibition(
    tmp_path: Path,
) -> None:
    """R2110 is a rule about what production code must NOT do, not a helper."""
    row = next(d for d in catalog(_cfg(tmp_path)) if d.name == "set_diff_classification")
    assert "set_diff_vs_head" not in row.value
    assert row.guarded_by == Path("tests/invariants/test_set_diff_for_auto_tool_classification.py")
