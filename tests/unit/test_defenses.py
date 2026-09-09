from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from agent_runner import events
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


def test_defense_should_be_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Defense)
    assert Defense.__dataclass_params__.frozen


def test_catalog_should_return_fifteen_entries(tmp_path: Path) -> None:
    cat = catalog(_cfg(tmp_path))

    assert len(cat) == 15


def test_catalog_entries_should_have_required_fields(tmp_path: Path) -> None:
    for d in catalog(_cfg(tmp_path)):
        assert d.name
        assert d.current_state in {"active", "degraded", "off"}


def test_round_timeout_defense_should_reflect_configured_value(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    object.__setattr__(cfg.runtime, "round_timeout_s", 999)

    cat = catalog(cfg)
    rt = next(d for d in cat if d.name == "round_timeout_s")

    assert rt.value == 999


def test_catalog_should_include_codified_incident_references(tmp_path: Path) -> None:
    cat = catalog(_cfg(tmp_path))
    incident_codes = " ".join(d.codifies or "" for d in cat)

    for code in ("R1128", "R725", "R820", "§9", "R2110", "R721"):
        assert code in incident_codes, f"defense catalog missing reference to {code}"


def test_catalog_guarded_by_paths_should_all_exist(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent

    for d in catalog(_cfg(tmp_path)):
        assert d.guarded_by is not None, f"defense {d.name} names no test that guards it"
        full = repo_root / d.guarded_by
        assert full.exists(), f"defense {d.name} references missing test {d.guarded_by}"


def test_defense_should_have_guarded_by_when_active(tmp_path: Path) -> None:
    """A defense claiming "active" with nothing behind it is an unverified claim.

    Deliberately not folded into the unconditional check above: guarded_by stays
    ``Path | None`` for plugin-supplied entries, so that check may one day have to
    weaken. This is the floor that must survive it. Both env variants are walked
    because critical_envs_injection is the one entry whose state depends on config —
    it reads "off" under the default empty ``[agent.env]``.
    """
    active_count = 0
    for cfg in (_cfg(tmp_path), _cfg(tmp_path, env={"DISABLE_AUTOUPDATER": "1"})):
        for d in catalog(cfg):
            if d.current_state == "active":
                active_count += 1
                assert d.guarded_by is not None, (
                    f"defense {d.name} claims active but names no test that guards it"
                )

    assert active_count > 0, "no defense reached current_state=='active' — test proves nothing"


def test_defense_names_should_be_unique(tmp_path: Path) -> None:
    names = [d.name for d in catalog(_cfg(tmp_path))]

    assert len(names) == len(set(names)), "duplicate defense names in catalog"


def test_critical_envs_injection_should_list_env_keys_when_agent_env_set(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, env={"DISABLE_AUTOUPDATER": "1", "FOO": "bar"})

    cat = catalog(cfg)
    row = next(d for d in cat if d.name == "critical_envs_injection")

    assert sorted(row.value) == ["DISABLE_AUTOUPDATER", "FOO"]
    assert row.current_state == "active"


def test_critical_envs_injection_should_be_off_when_agent_env_empty(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, env={})

    cat = catalog(cfg)
    row = next(d for d in cat if d.name == "critical_envs_injection")

    assert row.value == []
    assert row.current_state == "off"


def test_sigterm_reaper_defense_should_name_graceful_stop_guard(
    tmp_path: Path,
) -> None:
    """R725 is delivered by serve's graceful-stop contract, not by a signal handler."""
    row = next(d for d in catalog(_cfg(tmp_path)) if d.name == "sigterm_reaper")

    assert "install_sigterm_reaper" not in row.value
    assert row.guarded_by == Path("tests/integration/test_serve_loop.py")


def test_set_diff_defense_should_name_prohibition_guard(
    tmp_path: Path,
) -> None:
    """R2110 is a rule about what production code must NOT do, not a helper."""
    row = next(d for d in catalog(_cfg(tmp_path)) if d.name == "set_diff_classification")

    assert "set_diff_vs_head" not in row.value
    assert row.guarded_by == Path("tests/invariants/test_set_diff_for_auto_tool_classification.py")


def test_catalog_docstring_should_state_no_count() -> None:
    """The catalog literal is the SSOT; a count restated in prose only drifts."""
    assert catalog.__doc__ is not None
    assert not re.search(r"\d", catalog.__doc__), (
        f"catalog docstring hardcodes a number: {catalog.__doc__!r}"
    )


def test_event_kind_registry_defense_value_should_track_builtin_kinds_when_registry_shrunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the count is computed, not re-hardcoded — a literal would not move."""
    monkeypatch.setattr(events, "_BUILTIN_KINDS", frozenset({"a", "b", "c"}))

    row = next(d for d in catalog(_cfg(tmp_path)) if d.name == "event_kind_registry")

    assert "3 built-in kinds" in row.value
