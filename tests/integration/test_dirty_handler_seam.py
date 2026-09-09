"""Integration test: dirty_handler seam wiring in runner.py.

Verifies that after routing through hooks.dispatch_dirty, the default plugin
emits dirty_auto_committed for a successful auto_commit round.

This event was NOT emitted by the old inline switch — so a RED run before the
Task-6 wiring change and GREEN after is the correct TDD signal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PhasesConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.runner import run_one_round
from tests._test_helpers import read_events_for_current_month


def _make_auto_commit_cfg(tmp_git_repo: Path, agent_script: Path) -> Config:
    """Config with dirty_action=auto_commit; log_dir and prompt outside work_dir."""
    sandbox = tmp_git_repo.parent / f"seam-sandbox-{tmp_git_repo.name}"
    sandbox.mkdir(exist_ok=True)
    log_dir = sandbox / "logs"
    prompt = sandbox / "prompt.md"
    # >= 500 bytes so prompt_smoke_passes startup check passes
    prompt.write_text("Substantive prompt content for seam test. " * 20)
    script_copy = sandbox / agent_script.name
    shutil.copy2(agent_script, script_copy)
    script_copy.chmod(0o755)
    return Config(
        agent=AgentConfig(command=[str(script_copy)], prompt_arg_template=[]),
        runtime=RuntimeConfig(
            work_dir=tmp_git_repo,
            log_dir=log_dir,
            # round_timeout_s=30 (widened from 10, 0.2.19): the fake agent's
            # "dirty" behavior is trivial (write a file, exit 0) but under
            # >=2 concurrent gates (~2-3x CPU oversubscription) the real
            # subprocess spawn was occasionally starved past a 10s wall and
            # killed before it ever ran, failing "round should succeed" for
            # a reason unrelated to the dirty_auto_committed wiring under
            # test -- see the same mechanism in test_agent_runtime.py's
            # test_given_prompt_arg_template_....
            round_timeout_s=30,
        ),
        prompt=PromptConfig(file=prompt, inject_context=True),
        vcs=VcsConfig(dirty_action="auto_commit"),
        phases=PhasesConfig(),
    )


def test_auto_commit_via_default_plugin_emits_dirty_auto_committed(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch,
) -> None:
    """auto_commit round routed through DefaultDirtyHandler emits dirty_auto_committed.

    RED state: old inline switch called vcs_state.try_auto_commit silently on
    success — no dirty_auto_committed event.
    GREEN state: dispatch_dirty → DefaultDirtyHandler emits the event.
    """
    # fake_agent_script writes $WORK_DIR/dirty.txt when FAKE_AGENT_BEHAVIOR=dirty
    monkeypatch.setenv("FAKE_AGENT_BEHAVIOR", "dirty")
    monkeypatch.setenv("WORK_DIR", str(tmp_git_repo))

    cfg = _make_auto_commit_cfg(tmp_git_repo, fake_agent_script)
    result = run_one_round(cfg)

    assert result.exit_code == 0, "round should succeed"

    # The DirtyOutcome is recorded on the RoundResult (fuller spec witness).
    assert result.dirty_outcome is not None
    assert result.dirty_outcome.kind == "committed"

    all_events = read_events_for_current_month(cfg.runtime.log_dir)
    assert any(e.get("event") == "dirty_auto_committed" for e in all_events), (
        "dirty_auto_committed event not found — DefaultDirtyHandler seam not wired"
    )
