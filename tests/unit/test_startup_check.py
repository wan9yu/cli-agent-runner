from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import startup_check
from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.startup_check import CheckResult, run_battery  # noqa: F401


def _cfg(tmp_git_repo: Path, prompt_text: str = "Long prompt body for testing." * 20) -> Config:
    log_dir = tmp_git_repo / "logs"
    prompt_file = tmp_git_repo / "p.md"
    prompt_file.write_text(prompt_text)
    return Config(
        agent=AgentConfig(command=["bash"], prompt_arg_template=["-c", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir),
        prompt=PromptConfig(file=prompt_file, inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )


def test_run_battery_should_pass_all_checks_when_config_valid(tmp_git_repo: Path) -> None:
    results = run_battery(_cfg(tmp_git_repo))

    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_run_battery_should_fail_prompt_check_when_prompt_file_missing(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    cfg.prompt.file.unlink()

    results = run_battery(cfg)

    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_file_exists" for r in failed)


def test_run_battery_should_fail_git_check_permanently_when_work_dir_not_git_repo(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)  # tmp_path is NOT a git repo

    results = run_battery(cfg)

    git = next(r for r in results if r.name == "work_dir_is_git_repo")
    assert git.ok is False
    assert git.permanent is True


def test_run_battery_should_degrade_git_check_cleanly_when_git_times_out(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GitTimeout out of is_git_repo (host under load / a hung git process)
    must degrade to a clean, environmental CheckResult -- like sibling
    _check_log_dir catches OSError -- instead of a raw traceback out of
    run_battery."""
    from agent_runner.vcs_state import GitTimeout

    def boom(_path):
        raise GitTimeout("git rev-parse --is-inside-work-tree exceeded 10s")

    monkeypatch.setattr("agent_runner.vcs_state.is_git_repo", boom)

    results = run_battery(_cfg(tmp_git_repo))

    failed = [r for r in results if not r.ok]
    git = next(r for r in failed if r.name == "work_dir_is_git_repo")
    assert "exceeded 10s" in git.reason
    assert git.permanent is False  # self-heals (hung git, not a broken config) -> environmental


def test_run_battery_should_fail_cli_check_when_agent_cli_not_in_path(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    object.__setattr__(cfg.agent, "command", ["definitely-nonexistent-cli-xyz"])

    results = run_battery(cfg)

    failed = [r for r in results if not r.ok]
    assert any(r.name == "agent_cli_in_path" for r in failed)


def test_run_battery_should_fail_smoke_check_when_prompt_starts_with_dash(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="-this-starts-with-dash" + "x" * 600)

    results = run_battery(cfg)

    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_smoke_passes" for r in failed)


def test_run_battery_should_fail_smoke_check_when_prompt_under_min_bytes(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="too short")

    results = run_battery(cfg)

    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_smoke_passes" for r in failed)


def test_run_battery_should_pass_smoke_check_when_prompt_has_yaml_frontmatter(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="---\ntitle: x\n---\n" + "Body content. " * 50)

    results = run_battery(cfg)

    failed = [r for r in results if not r.ok]
    assert not any(r.name == "prompt_smoke_passes" for r in failed)


def test_run_battery_should_return_empty_when_escape_hatch_env_set(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_RUNNER_SKIP_STARTUP_CHECK", "1")
    cfg = _cfg(tmp_git_repo)
    cfg.prompt.file.unlink()  # would normally fail

    results = run_battery(cfg)

    assert results == []


def test_run_battery_should_validate_relative_command_against_work_dir_when_cwd_differs(
    tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """./relative agent commands exec in work_dir (cwd= spawn fix), so the CLI
    check must validate against work_dir — not the supervisor's cwd."""
    cfg = _cfg(tmp_git_repo)
    script = tmp_git_repo / "agent.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    object.__setattr__(cfg.agent, "command", ["./agent.sh"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # supervisor cwd deliberately != work_dir

    results = run_battery(cfg)

    cli = next(r for r in results if r.name == "agent_cli_in_path")
    assert cli.ok, cli.reason


def test_run_battery_should_fail_cli_check_when_relative_command_missing_in_work_dir(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    object.__setattr__(cfg.agent, "command", ["./no-such-agent.sh"])

    results = run_battery(cfg)

    cli = next(r for r in results if r.name == "agent_cli_in_path")
    assert not cli.ok
    assert str(tmp_git_repo) in cli.reason


def test_all_check_kinds_should_match_battery_kinds_when_config_is_valid(
    tmp_git_repo: Path,
) -> None:
    """Pin startup_check.all_check_kinds() -- the SSOT the defenses catalog's
    startup_smoke_check entry renders its count from -- against what
    run_battery() actually emits for a base (no-phase-override) config, so
    the two can't silently drift apart the way the old hand-written "6
    checks" string did."""
    results = run_battery(_cfg(tmp_git_repo))

    battery_kinds = {r.name.split(":", 1)[0] for r in results}
    assert battery_kinds == set(startup_check.all_check_kinds())


def test_checkresult_permanent_should_default_to_false(tmp_git_repo: Path) -> None:
    # Unclassified checks are environmental by default (locked decision).
    assert CheckResult("x", ok=False, reason="r").permanent is False


def test_run_battery_should_mark_log_dir_check_environmental_when_write_fails(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "mkdir", boom)

    failed = [r for r in run_battery(_cfg(tmp_git_repo)) if not r.ok]

    log = next(r for r in failed if r.name == "log_dir_writable")
    assert log.permanent is False  # ENOSPC → recoverable → environmental


def test_stdin_container_check_should_fail_permanent_when_container_prefix_lacks_interactive() -> (
    None
):
    agent = AgentConfig(
        command=["pi", "--mode", "json"],
        prompt_arg_template=[],
        prompt_delivery="stdin",
        exec_prefix=["docker", "run", "--rm", "img"],  # no -i
    )

    result = startup_check._check_stdin_container_interactive(
        agent, Path("/srv"), "stdin_container_interactive"
    )

    assert not result.ok
    assert result.permanent  # silent hang otherwise → fail loud at boot


def test_stdin_container_check_should_pass_when_interactive_present() -> None:
    agent = AgentConfig(
        command=["pi", "--mode", "json"],
        prompt_arg_template=[],
        prompt_delivery="stdin",
        exec_prefix=["docker", "run", "--rm", "-i", "img"],
    )

    assert startup_check._check_stdin_container_interactive(agent, Path("/srv"), "n").ok


def test_stdin_container_check_should_fail_permanent_when_only_command_has_interactive_flag() -> (
    None
):
    # The agent's OWN -i (in command, which runs INSIDE the container) must not
    # satisfy the check -- only exec_prefix's own flags control whether docker
    # forwards stdin.
    agent = AgentConfig(
        command=["mycli", "-i", "x"],
        prompt_arg_template=[],
        prompt_delivery="stdin",
        exec_prefix=["docker", "run", "--rm", "img"],  # no -i here
    )

    result = startup_check._check_stdin_container_interactive(
        agent, Path("/srv"), "stdin_container_interactive"
    )

    assert not result.ok
    assert result.permanent


def test_run_battery_should_fail_named_phase_check_when_phase_prompt_override_missing(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.config import PhaseOverride, PhasesConfig

    cfg = _cfg(tmp_git_repo)
    missing = tmp_git_repo / "no-such-phase-prompt.md"
    object.__setattr__(
        cfg,
        "phases",
        PhasesConfig(list=["dev"], overrides={"dev": PhaseOverride(prompt_files=[missing])}),
    )

    failed = [r for r in run_battery(cfg) if not r.ok]

    dev = next(r for r in failed if r.name == "prompt_smoke_passes:dev")
    assert dev.permanent is True


def test_control_plane_check_should_fail_permanent_when_log_dir_is_inside_a_containerized_work_dir():  # noqa: E501 — full name states the exact condition; BDD naming wins over line-length here
    agent = AgentConfig(
        command=["pi", "--mode", "json"],
        prompt_arg_template=[],
        exec_prefix=["docker", "run", "--rm", "-i", "img"],
    )

    result = startup_check._check_control_plane_outside_container(
        agent, Path("/srv/proj"), Path("/srv/proj/logs"), "control_plane_outside_container"
    )

    assert not result.ok
    assert result.permanent


def test_control_plane_check_should_pass_when_log_dir_is_outside_work_dir():
    agent = AgentConfig(
        command=["pi", "--mode", "json"],
        prompt_arg_template=[],
        exec_prefix=["docker", "run", "--rm", "-i", "img"],
    )

    assert startup_check._check_control_plane_outside_container(
        agent, Path("/srv/proj"), Path("/home/u/.agent-runner/proj/logs"), "n"
    ).ok


def test_control_plane_check_should_pass_when_not_containerized_even_if_log_dir_is_inside():
    agent = AgentConfig(
        command=["pi", "--mode", "json"], prompt_arg_template=[], exec_prefix=["nice", "-n", "10"]
    )

    assert startup_check._check_control_plane_outside_container(
        agent, Path("/srv/proj"), Path("/srv/proj/logs"), "n"
    ).ok
