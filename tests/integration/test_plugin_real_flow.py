"""Real-flow canary tests for built-in plugins.

These tests run real `agent-runner serve --max-rounds 1` with fake-agent
shell scripts that emit CLI-shaped JSONL on stdout. They verify that
`agent_usage_recorded` events actually fire end-to-end — the canary
that would have caught the 0.1.20-0.1.24 plugin path bug (where unit
tests pre-seeded the wrong path and matched the plugin's wrong-path
assumption, masking production behavior).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests._test_helpers import read_events_for_current_month


def _init_git(work_dir: Path) -> None:
    """Initialize a minimal git repo so startup checks pass."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=work_dir, check=True)
    (work_dir / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=work_dir,
        check=True,
    )


def _write_minimal_toml(work_dir: Path, agent_command: str, agent_name: str) -> Path:
    """Write a minimal TOML config for one integration round.

    Initialises a git repo (required by the startup smoke check) and pads
    the prompt to exceed the 500-byte minimum.  Agent command is a shell
    script path; the fake agent emits CLI-shaped JSONL and exits 0.
    """
    _init_git(work_dir)
    prompts_dir = work_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    prompt_file = prompts_dir / "main.md"
    prompt_file.write_text(
        "# Real-flow integration test prompt\n\n"
        "This prompt is unused — the fake agent ignores its input and just\n"
        "prints pre-baked JSONL on stdout. We're testing the supervisor\n"
        "agent plugin path-reading flow end-to-end.\n\n" + ("Filler. " * 100)
    )
    log_dir = work_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    cfg_path = work_dir / "agent-runner.toml"
    cfg_path.write_text(
        f"[agent]\n"
        f'command = ["{agent_command}"]\n'
        f"prompt_arg_template = []\n"
        f'name = "{agent_name}"\n'
        f"[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{log_dir}"\n'
        f"restart_delay_s = 1\n"
        f"[prompt]\n"
        f'file = "{prompt_file}"\n'
    )
    return cfg_path


def test_given_real_serve_with_fake_claude_agent_when_round_completes_then_usage_event_fires(
    tmp_path: Path,
) -> None:
    """Canary: real flow emits agent_usage_recorded for claude.

    Would have caught the 0.1.20-0.1.24 plugin-path bug if it had existed then.
    Unit tests pre-seeded round-N.log directly (the wrong path that matched the
    plugin's wrong-path assumption); production behavior diverged silently.
    This test runs real agent-runner serve and checks the actual events file.
    """
    fake_agent = tmp_path / "fake-claude.sh"
    fake_agent.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"type":"system","subtype":"init"}\n'
        '{"type":"result","subtype":"success","is_error":false,'
        '"total_cost_usd":0.05,'
        '"message":{"model":"claude-opus-4-7"},'
        '"usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":80},'
        '"duration_ms":1234}\n'
        "EOF\n"
    )
    fake_agent.chmod(0o755)

    cfg_path = _write_minimal_toml(tmp_path, str(fake_agent), "claude")
    log_dir = tmp_path / "logs"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"serve failed\nstdout={proc.stdout[:500]}\nstderr={proc.stderr[:500]}"
    )

    events = read_events_for_current_month(log_dir)
    usage = [e for e in events if e.get("event") == "agent_usage_recorded"]
    assert len(usage) == 1, f"expected 1 usage event from real flow, got {usage}"
    assert usage[0]["agent"] == "claude"
    assert usage[0]["model"] == "claude-opus-4-7"
    # NET semantic: input_tokens = 100 - 80 = 20
    assert usage[0]["input_tokens"] == 20
    assert usage[0]["cached_tokens"] == 80
    assert usage[0]["output_tokens"] == 50
    assert usage[0]["cost_usd"] == 0.05


def test_given_real_serve_with_fake_gemini_agent_when_round_completes_then_usage_event_fires(
    tmp_path: Path,
) -> None:
    """Canary: real flow emits agent_usage_recorded for gemini."""
    fake_agent = tmp_path / "fake-gemini.sh"
    fake_agent.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"type":"result","timestamp":"2026-05-17T00:00:00Z","status":"success",'
        '"stats":{"total_tokens":1000,"input_tokens":800,"output_tokens":50,'
        '"cached":200,"duration_ms":5337,"tool_calls":0,'
        '"models":{"gemini-3-flash-preview":{"total_tokens":1000,"input_tokens":800,'
        '"output_tokens":50,"cached":200}}}}\n'
        "EOF\n"
    )
    fake_agent.chmod(0o755)

    cfg_path = _write_minimal_toml(tmp_path, str(fake_agent), "gemini")
    log_dir = tmp_path / "logs"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"serve failed\nstdout={proc.stdout[:500]}\nstderr={proc.stderr[:500]}"
    )

    events = read_events_for_current_month(log_dir)
    usage = [e for e in events if e.get("event") == "agent_usage_recorded"]
    assert len(usage) == 1, f"expected 1 usage event from real flow, got {usage}"
    assert usage[0]["agent"] == "gemini"
    assert usage[0]["model"] == "gemini-3-flash-preview"
    # NET: input_tokens = 800 - 200 = 600
    assert usage[0]["input_tokens"] == 600
    assert usage[0]["cached_tokens"] == 200
    assert usage[0]["output_tokens"] == 50
    assert usage[0]["cost_usd"] is None  # gemini has no USD field
