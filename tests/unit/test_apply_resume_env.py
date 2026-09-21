"""Cross-round resume, both halves.

The PRODUCER half (serve): ``_apply_resume_env`` resolves this phase's resume
flag from the plugin registry (``resolve_resume_flag``), mints a uuid on first
use or a fresh-eyes round, and publishes both via AGENT_RUNNER_RESUME_FLAG /
AGENT_RUNNER_RESUME_SESSION_ID -- popping both keys when the agent isn't
resume-capable or the phase is ambiguous, so a prior resume-capable phase's id
never leaks onto this round's child argv. ``session_resumed`` is emitted only
when an existing id is reused, never on a fresh mint.

The CONSUMER half (round child): the child never mints or derives a session id
-- it only copies serve's published env verbatim, and
``runner._resolve_resume_args`` returns ``[]`` unless BOTH env vars are
present and non-empty, so env-absence (systemd / standalone `round`, no serve
parent) is cold-start by construction, never a partial append.
"""

from __future__ import annotations

import dataclasses
import json

from agent_runner import agent_runtime, events, runner
from agent_runner._plugin_manifest import _LOADED_MANIFESTS, PluginManifest, register_manifest
from agent_runner.cli import serve_cmd
from agent_runner.config import AgentConfig, PhaseOverride, PhasesConfig
from tests._test_helpers import isolating, make_cfg

_reset = isolating(_LOADED_MANIFESTS)


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        for line in f.read_text().splitlines():
            out.append(json.loads(line))
    return out


def test_resolve_resume_args_should_return_flag_and_id_when_both_env_present(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_RESUME_FLAG", "--session-id")
    monkeypatch.setenv("AGENT_RUNNER_RESUME_SESSION_ID", "abc-123")

    assert runner._resolve_resume_args() == ["--session-id", "abc-123"]


def test_resolve_resume_args_should_return_empty_when_either_env_absent(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_RESUME_FLAG", "--session-id")
    monkeypatch.delenv("AGENT_RUNNER_RESUME_SESSION_ID", raising=False)

    assert runner._resolve_resume_args() == []


def test_resolve_resume_args_should_return_empty_when_both_env_absent(monkeypatch):
    monkeypatch.delenv("AGENT_RUNNER_RESUME_FLAG", raising=False)
    monkeypatch.delenv("AGENT_RUNNER_RESUME_SESSION_ID", raising=False)

    assert runner._resolve_resume_args() == []  # cold-start by construction


def test_run_should_append_resume_args_after_command_and_before_prompt_args_when_invoked(tmp_path):
    argv_dump = tmp_path / "argv.txt"
    script = tmp_path / "fake-agent.sh"
    script.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "$@" > "{argv_dump}"\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = agent_runtime.run(
        command=[str(script)],
        prompt_arg_template=["--prompt", "{prompt}"],
        prompt="hello",
        timeout_s=30,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        resume_args=("--session-id", "sid-9"),
    )

    assert result.exit_code == 0
    assert argv_dump.read_text(encoding="utf-8").splitlines() == [
        "--session-id",
        "sid-9",
        "--prompt",
        "hello",
    ]


def _resume_capable_cfg(tmp_path):
    register_manifest(PluginManifest(name="resume_agent", resume_flag="--session-id"))
    cfg = make_cfg(tmp_path)
    return dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["resume_agent"]))


def test_apply_resume_env_should_publish_flag_and_a_minted_id_when_agent_is_resume_capable(
    tmp_path,
):
    cfg = _resume_capable_cfg(tmp_path)
    round_env: dict = {}
    session_ids: dict = {}

    serve_cmd._apply_resume_env(cfg, round_env, None, False, session_ids, tmp_path)

    assert round_env["AGENT_RUNNER_RESUME_FLAG"] == "--session-id"
    assert round_env["AGENT_RUNNER_RESUME_SESSION_ID"]
    assert round_env["AGENT_RUNNER_RESUME_SESSION_ID"] == session_ids[None]


def test_apply_resume_env_should_pop_stale_keys_when_a_non_resume_phase_follows_a_resume_phase(
    tmp_path,
):
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["kimi"]))
    round_env = {
        "AGENT_RUNNER_RESUME_FLAG": "--session-id",
        "AGENT_RUNNER_RESUME_SESSION_ID": "stale-id",
    }
    session_ids: dict = {}

    serve_cmd._apply_resume_env(cfg, round_env, None, False, session_ids, tmp_path)

    assert "AGENT_RUNNER_RESUME_FLAG" not in round_env
    assert "AGENT_RUNNER_RESUME_SESSION_ID" not in round_env


def test_apply_resume_env_should_pop_both_keys_when_phase_arg_is_none_and_phases_list_is_nonempty(
    tmp_path,
):
    cfg = _resume_capable_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["role_a", "role_b"],
            overrides={
                "role_a": PhaseOverride(
                    agent=AgentConfig(command=["resume_agent"], prompt_arg_template=["{prompt}"])
                ),
                "role_b": PhaseOverride(
                    agent=AgentConfig(command=["resume_agent"], prompt_arg_template=["{prompt}"])
                ),
            },
        ),
    )
    round_env = {
        "AGENT_RUNNER_RESUME_FLAG": "--session-id",
        "AGENT_RUNNER_RESUME_SESSION_ID": "stale-id",
    }
    session_ids: dict = {}

    # serve did not pin the phase (--ignore-schedule / phase_policy=wait) but
    # [phases] exists, so the round child self-rotates -- serve cannot know
    # which agent will actually run this round.
    serve_cmd._apply_resume_env(cfg, round_env, None, False, session_ids, tmp_path)

    assert "AGENT_RUNNER_RESUME_FLAG" not in round_env
    assert "AGENT_RUNNER_RESUME_SESSION_ID" not in round_env


def test_apply_resume_env_should_reuse_the_same_id_across_rounds_for_one_phase_when_invoked(
    tmp_path,
):
    cfg = _resume_capable_cfg(tmp_path)
    session_ids: dict = {}

    first_env: dict = {}
    serve_cmd._apply_resume_env(cfg, first_env, None, False, session_ids, tmp_path)
    second_env: dict = {}
    serve_cmd._apply_resume_env(cfg, second_env, None, False, session_ids, tmp_path)

    assert (
        second_env["AGENT_RUNNER_RESUME_SESSION_ID"] == first_env["AGENT_RUNNER_RESUME_SESSION_ID"]
    )


def test_apply_resume_env_should_mint_a_new_id_on_a_fresh_eyes_round_when_invoked(tmp_path):
    cfg = _resume_capable_cfg(tmp_path)
    session_ids: dict = {}

    first_env: dict = {}
    serve_cmd._apply_resume_env(cfg, first_env, None, False, session_ids, tmp_path)
    fresh_env: dict = {}
    serve_cmd._apply_resume_env(cfg, fresh_env, None, True, session_ids, tmp_path)

    assert (
        fresh_env["AGENT_RUNNER_RESUME_SESSION_ID"] != first_env["AGENT_RUNNER_RESUME_SESSION_ID"]
    )


def test_apply_resume_env_should_key_ids_by_phase_so_two_phases_get_distinct_ids_when_invoked(
    tmp_path,
):
    cfg = _resume_capable_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["role_a", "role_b"],
            overrides={
                "role_a": PhaseOverride(
                    agent=AgentConfig(command=["resume_agent"], prompt_arg_template=["{prompt}"])
                ),
                "role_b": PhaseOverride(
                    agent=AgentConfig(command=["resume_agent"], prompt_arg_template=["{prompt}"])
                ),
            },
        ),
    )
    session_ids: dict = {}

    env_a: dict = {}
    serve_cmd._apply_resume_env(cfg, env_a, "role_a", False, session_ids, tmp_path)
    env_b: dict = {}
    serve_cmd._apply_resume_env(cfg, env_b, "role_b", False, session_ids, tmp_path)

    # Same binary, different phases -- keyed by phase, NOT binary.
    assert env_a["AGENT_RUNNER_RESUME_SESSION_ID"] != env_b["AGENT_RUNNER_RESUME_SESSION_ID"]


def test_apply_resume_env_should_emit_session_resumed_only_when_resuming_not_on_mint(tmp_path):
    cfg = _resume_capable_cfg(tmp_path)
    session_ids: dict = {}

    serve_cmd._apply_resume_env(cfg, {}, None, False, session_ids, tmp_path)

    assert _events(tmp_path) == []

    round_env: dict = {}
    serve_cmd._apply_resume_env(cfg, round_env, None, False, session_ids, tmp_path)

    evs = _events(tmp_path)
    assert [e["event"] for e in evs] == [events.SESSION_RESUMED]
    assert evs[0]["session_id"] == round_env["AGENT_RUNNER_RESUME_SESSION_ID"]
    assert "phase" not in evs[0]

    serve_cmd._apply_resume_env(cfg, {}, None, True, session_ids, tmp_path)

    assert [e["event"] for e in _events(tmp_path)] == [events.SESSION_RESUMED]


def test_apply_resume_env_should_include_phase_in_session_resumed_when_phase_is_set(tmp_path):
    cfg = _resume_capable_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["role_a"],
            overrides={
                "role_a": PhaseOverride(
                    agent=AgentConfig(command=["resume_agent"], prompt_arg_template=["{prompt}"])
                ),
            },
        ),
    )
    session_ids: dict = {}

    serve_cmd._apply_resume_env(cfg, {}, "role_a", False, session_ids, tmp_path)
    serve_cmd._apply_resume_env(cfg, {}, "role_a", False, session_ids, tmp_path)

    evs = _events(tmp_path)
    assert evs[0]["phase"] == "role_a"
