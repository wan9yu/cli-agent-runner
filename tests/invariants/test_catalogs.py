"""Defense catalog and dependency invariants."""

from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
REPO = PKG.parent


def test_given_defenses_catalog_when_loaded_then_each_entry_has_required_fields() -> None:
    from agent_runner.config import (
        AgentConfig,
        Config,
        PromptConfig,
        RuntimeConfig,
        VcsConfig,
    )
    from agent_runner.defenses import catalog

    cfg = Config(
        agent=AgentConfig(command=["x"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=Path("/tmp"), log_dir=Path("/tmp/logs")),
        prompt=PromptConfig(file=Path("/tmp/p.md"), inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
    cat = catalog(cfg)
    for d in cat:
        assert d.name and isinstance(d.name, str)
        assert d.current_state in {"active", "degraded", "off"}


def test_given_defenses_invariant_paths_when_resolved_then_all_exist_or_none() -> None:
    from agent_runner.config import (
        AgentConfig,
        Config,
        PromptConfig,
        RuntimeConfig,
        VcsConfig,
    )
    from agent_runner.defenses import catalog

    cfg = Config(
        agent=AgentConfig(command=["x"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=Path("/tmp"), log_dir=Path("/tmp/logs")),
        prompt=PromptConfig(file=Path("/tmp/p.md"), inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
    for d in catalog(cfg):
        if d.guarded_by is None:
            continue
        full = REPO / d.guarded_by
        assert full.exists(), f"{d.name} references missing test {d.guarded_by}"


def test_given_codebase_when_scanned_then_no_paramiko_or_fabric_runtime_deps() -> None:
    """ssh stays subprocess-based; paramiko/fabric only allowed in tests/e2e."""
    offenders: list[tuple[str, str]] = []
    for f in PKG.rglob("*.py"):
        text = f.read_text()
        for forbidden in ("paramiko", "fabric"):
            if f"import {forbidden}" in text or f"from {forbidden}" in text:
                offenders.append((str(f.relative_to(REPO)), forbidden))
    assert offenders == [], f"runtime modules import paramiko/fabric: {offenders}"


def test_given_pyproject_when_read_then_e2e_extra_declares_no_ssh_library() -> None:
    """The e2e suite is pure subprocess ssh (tests/e2e/conftest.py:21-27), and
    test_catalogs' sibling invariant forbids paramiko/fabric in source. A declared
    dep nothing imports still resolves invoke+paramiko+cryptography+bcrypt+pynacl
    for every e2e contributor, and [e2e] is published PyPI metadata."""
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    e2e = data["project"]["optional-dependencies"]["e2e"]
    for forbidden in ("paramiko", "fabric"):
        assert not any(forbidden in dep for dep in e2e), (
            f"pyproject [e2e] declares {forbidden}, which nothing imports and "
            f"test_catalogs.py forbids in source"
        )
