from __future__ import annotations

from pathlib import Path

import pytest

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


def _toml(tmp_path: Path) -> Path:
    """The toml path most tests here load ``cfg`` "from" — co-located with
    work_dir, matching the old (pre-Group-C) implicit assumption. The
    divergent case (work_dir != toml dir) gets its own test below."""
    return tmp_path / "agent-runner.toml"


def test_serve_unit_filename_should_contain_project_name_when_built(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    project = cfg.runtime.work_dir.name

    assert serve_unit_filename(project) == f"agent-runner@{project}.service"


def test_monitor_unit_filename_should_return_project_scoped_unit_name_when_built(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    project = cfg.runtime.work_dir.name

    assert monitor_unit_filename(project) == f"agent-runner-monitor@{project}.service"
    assert monitor_unit_filename(project) != serve_unit_filename(project)


def test_render_serve_unit_should_contain_required_sections_when_rendered(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"

    body = render_serve_unit(cfg, script_path=script_path, config_path=_toml(tmp_path))

    from agent_runner.api import CRASH_LOOP_EXIT, MEM_LOOP_PERSISTENT_EXIT, PERMANENT_CONFIG_EXIT

    for needle in (
        "[Unit]",
        "[Service]",
        "[Install]",
        "Restart=on-failure",
        f"RestartPreventExitStatus={PERMANENT_CONFIG_EXIT} {CRASH_LOOP_EXIT} "
        f"{MEM_LOOP_PERSISTENT_EXIT}",
        "KillSignal=SIGTERM",
    ):
        assert needle in body, f"missing {needle!r} in unit body"
    assert "Restart=always" not in body  # a deliberate stop must not auto-restart


def test_render_serve_unit_should_restrict_restart_prevention_to_giveup_exits_when_rendered(
    tmp_path: Path,
) -> None:
    """0.2.16 Task 5: MEM_LOOP_PERSISTENT_EXIT (a deliberate cross-restart
    give-up) is in RestartPreventExitStatus so systemd STOPS; MEM_LOOP_EXIT
    (71, break-then-restart) stays absent so systemd keeps restarting it."""
    from agent_runner._serve_policy import (
        CRASH_LOOP_EXIT,
        MEM_LOOP_EXIT,
        MEM_LOOP_PERSISTENT_EXIT,
        PERMANENT_CONFIG_EXIT,
    )

    cfg = _cfg(tmp_path)

    unit = render_serve_unit(
        cfg, script_path=tmp_path / "ar", config_path=tmp_path / "agent-runner.toml"
    )

    prevented = unit.split("RestartPreventExitStatus=")[1].split("\n")[0].split()
    assert str(MEM_LOOP_PERSISTENT_EXIT) in prevented
    assert str(PERMANENT_CONFIG_EXIT) in prevented
    assert str(CRASH_LOOP_EXIT) in prevented
    assert str(MEM_LOOP_EXIT) not in prevented


@pytest.mark.parametrize(
    "round_timeout_s,expected",
    [(600, 810), (1800, 2010)],
    ids=["timeout-600", "timeout-1800"],
)
def test_render_serve_unit_should_add_grace_budget_to_timeout_when_rendered(
    tmp_path: Path, round_timeout_s: int, expected: int
) -> None:
    """TimeoutStopSec = round_timeout_s + 210 budget (_serve_policy.timeout_budget)."""
    cfg = _cfg(tmp_path, round_timeout_s=round_timeout_s)

    body = render_serve_unit(
        cfg, script_path=tmp_path / ".venv" / "bin" / "agent-runner", config_path=_toml(tmp_path)
    )

    assert f"TimeoutStopSec={expected}" in body


def test_render_serve_unit_should_substitute_paths_when_rendered(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"

    body = render_serve_unit(cfg, script_path=script_path, config_path=_toml(tmp_path))

    assert str(cfg.runtime.work_dir) in body
    assert f"{script_path} serve" in body


def test_render_serve_unit_should_use_given_config_path_verbatim_in_execstart_when_rendered(
    tmp_path: Path,
) -> None:
    """ExecStart's --config must be the toml the caller actually loaded ``cfg``
    from, not a re-derivation of cfg.runtime.work_dir / "agent-runner.toml"."""
    cfg = _cfg(tmp_path)
    explicit = tmp_path / "somewhere-else.toml"

    body = render_serve_unit(cfg, script_path=tmp_path / "ar", config_path=explicit)

    assert f"--config {explicit}\n" in body
    assert str(_toml(tmp_path)) not in body


def test_render_units_should_use_real_toml_config_path_when_work_dir_differs_from_toml_dir(
    tmp_path: Path,
) -> None:
    """Group C regression (spec-review finding): runtime.work_dir
    is independently configurable and can point anywhere -- an absolute value is
    legal and only a *relative* one anchors to the toml's own directory
    (config.py's loader). render_serve_unit/render_monitor_unit must embed
    --config <where the toml actually is>, never <work_dir>/agent-runner.toml."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "p.md").write_text("hi")
    real_toml = tmp_path / "configs" / "agent-runner.toml"
    cfg = Config(
        agent=AgentConfig(command=["my-agent"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(
            work_dir=repo_dir,  # deliberately NOT real_toml.parent
            log_dir=repo_dir / "logs",
            round_timeout_s=600,
        ),
        prompt=PromptConfig(file=repo_dir / "p.md", inject_context=True),
        vcs=VcsConfig(),
    )
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"

    serve_body = render_serve_unit(cfg, script_path=script_path, config_path=real_toml)
    monitor_body = render_monitor_unit(cfg, script_path=script_path, config_path=real_toml)

    wrong_guess = repo_dir / "agent-runner.toml"
    for body in (serve_body, monitor_body):
        assert f"--config {real_toml}\n" in body
        assert str(wrong_guess) not in body
        # WorkingDirectory still reflects the project's own work_dir, unaffected
        assert f"WorkingDirectory={repo_dir}\n" in body


def test_render_monitor_unit_should_run_monitor_command_when_rendered(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    script_path = tmp_path / ".venv" / "bin" / "agent-runner"

    body = render_monitor_unit(cfg, script_path=script_path, config_path=_toml(tmp_path))

    assert f"{script_path} monitor" in body
    assert str(cfg.runtime.work_dir) in body


def test_render_serve_unit_should_use_max_round_timeout_across_phases_when_phase_override_present(
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

    unit = render_serve_unit(
        cfg, script_path=tmp_path / ".venv" / "bin" / "agent-runner", config_path=_toml(tmp_path)
    )

    # max(1800, 3600) + 210 = 3810
    assert "TimeoutStopSec=3810" in unit


def test_render_serve_unit_should_include_user_directive_when_user_arg_given(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)

    body = render_serve_unit(
        cfg, script_path=tmp_path / "ar", config_path=_toml(tmp_path), user="dietpi"
    )

    assert "User=dietpi" in body
    assert "Group=dietpi" in body
    assert "WantedBy=multi-user.target" in body


def test_render_serve_unit_should_omit_user_directive_when_no_user_arg_given(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)

    body = render_serve_unit(cfg, script_path=tmp_path / "ar", config_path=_toml(tmp_path))

    assert "User=" not in body
    assert "WantedBy=default.target" in body


def test_render_serve_unit_should_set_killmode_mixed_when_rendered(tmp_path: Path) -> None:
    """KillMode=mixed is the load-bearing half of graceful drain: systemd's
    default control-group KillMode SIGTERMs the whole cgroup — round and agent
    child included — so serve would never get to drain the current round.
    Verified in production by a downstream integrator via an interrupted round."""
    body = render_serve_unit(
        _cfg(tmp_path), script_path=Path("/usr/bin/agent-runner"), config_path=_toml(tmp_path)
    )

    assert "KillMode=mixed" in body


def test_render_serve_unit_should_bound_restart_with_startlimit_when_rendered(
    tmp_path: Path,
) -> None:
    """RestartSec=3 alone can respawn a broken serve indefinitely; the StartLimit
    window converts a persistent early-exit into a systemd 'failed' state."""
    body = render_serve_unit(
        _cfg(tmp_path), script_path=tmp_path / "ar", config_path=_toml(tmp_path)
    )

    assert "StartLimitIntervalSec=300" in body
    assert "StartLimitBurst=5" in body
    # StartLimit* are [Unit] directives — must precede [Service].
    assert body.index("StartLimitBurst") < body.index("[Service]")
