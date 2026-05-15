from __future__ import annotations

from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PhaseOverride,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.service_unit import (
    monitor_unit_filename,
    render_monitor_unit,
    render_serve_unit,
    serve_unit_filename,
)


def _cfg(
    tmp_path: Path,
    *,
    round_timeout_s: int = 600,
    phases: list[str] | None = None,
) -> Config:
    return Config(
        agent=AgentConfig(command=["my-agent"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
            round_timeout_s=round_timeout_s,
        ),
        prompt=PromptConfig(file=tmp_path / "p.md", inject_context=True),
        vcs=VcsConfig(),
        phases=phases,
    )


def test_given_serve_unit_filename_when_built_then_contains_project_name(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    project = cfg.runtime.work_dir.name
    assert serve_unit_filename(project) == f"agent-runner@{project}.service"


def test_given_monitor_unit_filename_when_built_then_distinct_from_serve(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    project = cfg.runtime.work_dir.name
    assert monitor_unit_filename(project) == f"agent-runner-monitor@{project}.service"


def test_given_serve_unit_when_rendered_then_contains_required_sections(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"
    body = render_serve_unit(cfg, script_path=script_path)
    for needle in ("[Unit]", "[Service]", "[Install]", "Restart=always", "KillSignal=SIGTERM"):
        assert needle in body, f"missing {needle!r} in unit body"


def test_given_serve_unit_when_rendered_then_timeout_includes_grace(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)  # round_timeout_s=600
    body = render_serve_unit(cfg, script_path=tmp_path / ".venv" / "bin" / "agent-runner")
    assert "TimeoutStopSec=660" in body  # 600 + 60 grace


def test_given_serve_unit_when_rendered_then_paths_substituted(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"
    body = render_serve_unit(cfg, script_path=script_path)
    assert str(cfg.runtime.work_dir) in body
    assert f"{script_path} serve" in body


def test_given_monitor_unit_when_rendered_then_runs_monitor_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"
    body = render_monitor_unit(cfg, script_path=script_path)
    assert f"{script_path} monitor" in body
    assert str(cfg.runtime.work_dir) in body


def test_given_round_timeout_when_render_then_timeout_includes_grace(tmp_path: Path) -> None:
    """TimeoutStopSec = round_timeout_s + 60 grace."""
    cfg = _cfg(tmp_path, round_timeout_s=1800)
    unit = render_serve_unit(cfg, script_path=tmp_path / ".venv" / "bin" / "agent-runner")
    assert "TimeoutStopSec=1860" in unit  # 1800 + 60


def test_given_per_phase_override_when_render_then_timeoutstopsec_uses_max(
    tmp_path: Path,
) -> None:
    """Per-phase round_timeout_s influences systemd TimeoutStopSec via max()."""
    cfg = Config(
        agent=AgentConfig(command=["x"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
            round_timeout_s=1800,
        ),
        prompt=PromptConfig(file=tmp_path / "p.md"),
        vcs=VcsConfig(),
        phases=PhasesConfig(
            list=["dev", "qa"],
            overrides={"dev": PhaseOverride(round_timeout_s=3600)},
        ),
    )

    unit = render_serve_unit(cfg, script_path=tmp_path / ".venv" / "bin" / "agent-runner")
    # max(1800, 3600) + 60 = 3660
    assert "TimeoutStopSec=3660" in unit


def test_given_user_arg_when_render_serve_unit_then_includes_user_directive(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    body = render_serve_unit(cfg, script_path=tmp_path / "ar", user="dietpi")
    assert "User=dietpi" in body
    assert "Group=dietpi" in body
    assert "WantedBy=multi-user.target" in body


def test_given_no_user_arg_when_render_serve_unit_then_no_user_directive(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    body = render_serve_unit(cfg, script_path=tmp_path / "ar")
    assert "User=" not in body
    assert "WantedBy=default.target" in body
