from __future__ import annotations

from pathlib import Path

import pytest


def _make_toml(tmp_path: Path) -> Path:
    """Write a minimal agent-runner.toml and return its path."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )
    return toml


def _is_pip_call(cmd) -> bool:
    """Return True when *cmd* is a ``sys.executable -m pip …`` invocation."""
    return len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "pip"


def test_given_upgrade_subcommand_with_target_when_main_then_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`agent-runner upgrade --target X.Y.Z` parses + dispatches to upgrade_cmd.cmd."""
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        captured["config"] = getattr(args, "config", None)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    toml_path = _make_toml(tmp_path)

    rc = main(["--config", str(toml_path), "upgrade", "--target", "0.1.99"])
    assert rc == 0
    assert captured["target"] == "0.1.99"


def test_given_upgrade_no_target_when_main_then_target_defaults_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`agent-runner upgrade` (no --target) → args.target is None (= latest)."""
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    toml_path = _make_toml(tmp_path)

    rc = main(["--config", str(toml_path), "upgrade"])
    assert rc == 0
    assert captured["target"] is None


def test_given_happy_path_when_run_upgrade_then_emits_service_upgraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: stop → pip install → smoke → start → emit service_upgraded."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 0.1.99\n", stderr=""
            )
        if "peek" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)
    assert rc == 0

    pip_calls = [c for c in call_log if _is_pip_call(c)]
    assert len(pip_calls) == 1
    assert "cli-agent-runner==0.1.99" in pip_calls[0]

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    upgrades = [p for p in payloads if p["event"] == "service_upgraded"]
    assert len(upgrades) == 1
    assert upgrades[0]["to_version"] == "0.1.99"
    assert "from_version" in upgrades[0]
    assert "duration_s" in upgrades[0]


def test_given_no_target_when_run_upgrade_then_pip_uses_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--target` not supplied → pip install --upgrade cli-agent-runner (no version pin)."""
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 0.1.50\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target=None, cfg_path=toml_path)
    assert rc == 0
    pip_calls = [c for c in call_log if _is_pip_call(c)]
    assert len(pip_calls) == 1
    assert "cli-agent-runner" in pip_calls[0]
    assert "==" not in " ".join(pip_calls[0])


def test_given_pip_install_fails_when_run_upgrade_then_no_event_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pip install rc!=0 → service stopped, no event emitted, exit 1."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="ERROR: Could not find package"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="bogus", cfg_path=toml_path)
    assert rc == 1

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        assert not any(p["event"].startswith("service_upgrad") for p in payloads)


def test_given_smoke_fails_when_run_upgrade_then_rollback_emits_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke peek fails → pip --force-reinstall <from> → sanity smoke → start → emit rolled_back."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            # 1st --version (smoke for new): version reads as 0.1.99
            # 2nd --version (sanity smoke for restored): reads as from_version
            if call_count[0] == 2:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="agent-runner 0.1.99\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        if "peek" in cmd:
            # peek FAILS — triggers rollback
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="KeyError: 'phases'"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)
    assert rc == 1  # rollback succeeded but upgrade failed

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    rolled_back = [p for p in payloads if p["event"] == "service_upgrade_rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0]["attempted_version"] == "0.1.99"
    assert rolled_back[0]["restored_version"] == __version__
    assert "KeyError" in rolled_back[0]["failure_reason"]
    assert "duration_s" in rolled_back[0]


def test_given_smoke_version_fails_when_run_upgrade_then_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke --version fails (not just peek) → still triggers rollback."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            if call_count[0] == 2:  # first --version after pip → fails
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="ImportError: cannot import 'foo'"
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)
    assert rc == 1

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    rolled_back = [p for p in payloads if p["event"] == "service_upgrade_rolled_back"]
    assert len(rolled_back) == 1
    assert "ImportError" in rolled_back[0]["failure_reason"]


def test_given_rollback_pip_uses_force_reinstall_with_from_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rollback pip command uses --force-reinstall and pins from_version."""
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    pip_calls = []
    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if _is_pip_call(cmd):
            pip_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            if call_count[0] == 2:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)

    assert len(pip_calls) == 2
    rollback_pip = pip_calls[1]
    assert "--force-reinstall" in rollback_pip
    assert f"cli-agent-runner=={__version__}" in rollback_pip


def test_given_rollback_pip_fails_when_run_upgrade_then_rollback_failed_event_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke fails AND rollback pip also fails → service_upgrade_rollback_failed + exit 2."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    pip_calls = [0]

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            pip_calls[0] += 1
            if pip_calls[0] == 1:  # initial install succeeds
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # rollback pip FAILS
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="No version satisfies requirement"
            )
        if "--version" in cmd:
            # Smoke fails → triggers rollback
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)
    assert rc == 2

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [p for p in payloads if p["event"] == "service_upgrade_rollback_failed"]
    assert len(failed) == 1
    assert failed[0]["attempted_version"] == "0.1.99"
    assert failed[0]["restore_target_version"] == __version__
    assert "No version" in failed[0]["failure_reason"]


def test_given_rollback_sanity_smoke_fails_when_run_upgrade_then_rollback_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rollback pip succeeds but sanity smoke fails → service_upgrade_rollback_failed."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            # ALL --version calls fail (including the sanity smoke after rollback)
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="cannot import"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)
    assert rc == 2

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [p for p in payloads if p["event"] == "service_upgrade_rollback_failed"]
    assert len(failed) == 1
    assert "sanity smoke failed" in failed[0]["failure_reason"]


def test_given_api_stop_raises_when_run_upgrade_then_fail_no_pip_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """api.stop raises → rc=1, pip NEVER called, no service_upgrad* events emitted."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def _raise_stop(_wd):
        raise RuntimeError("systemctl failed")

    monkeypatch.setattr(api, "stop", _raise_stop)

    pip_called = []

    def fake_run(cmd, **kwargs):
        pip_called.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml)
    assert rc == 1

    # pip must NOT have been called
    assert not any(_is_pip_call(c) for c in pip_called), "pip was called despite api.stop raising"

    # no service_upgrad* events
    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        assert not any(p["event"].startswith("service_upgrad") for p in payloads)


def test_given_api_start_raises_after_smoke_when_run_upgrade_then_rollback_failed_event_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """api.start raises after smoke → rollback_failed event with restore_target_version=to_version.

    Exit code must be 2.
    """
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def _raise_start(_wd):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", _raise_start)

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 0.1.99\n", stderr=""
            )
        if "peek" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml)
    assert rc == 2

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files, "expected at least one events file"
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [p for p in payloads if p["event"] == "service_upgrade_rollback_failed"]
    assert len(failed) == 1
    assert failed[0]["restore_target_version"] == "0.1.99"
    assert "api.start raised after upgrade" in failed[0]["failure_reason"]


def test_given_empty_target_when_run_upgrade_then_fail_no_stop_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--target '' (empty string) → rc=1, api.stop NEVER called, no events."""
    import json

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml = _make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    stop_called = []
    monkeypatch.setattr(api, "stop", lambda _wd: stop_called.append(True))

    cfg = load_config(toml)
    rc = upgrade_cmd._run_upgrade(cfg, target="", cfg_path=toml)
    assert rc == 1

    assert not stop_called, "api.stop was called despite empty target"

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    if events_files:
        payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
        assert not any(p["event"].startswith("service_upgrad") for p in payloads)
