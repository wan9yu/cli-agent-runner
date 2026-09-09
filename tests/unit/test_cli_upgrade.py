from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml


def _is_pip_call(cmd) -> bool:
    """Return True when *cmd* is a ``sys.executable -m pip …`` invocation."""
    return len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "pip"


def test_main_should_dispatch_to_upgrade_cmd_when_upgrade_subcommand_has_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        captured["config"] = getattr(args, "config", None)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    toml_path = make_toml(tmp_path)

    rc = main(["--config", str(toml_path), "upgrade", "--target", "0.1.99"])

    assert rc == 0
    assert captured["target"] == "0.1.99"


def test_main_should_default_target_to_none_when_upgrade_subcommand_has_no_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``target=None`` means "use latest"."""
    from agent_runner.cli import main, upgrade_cmd

    captured = {}

    def fake_cmd(args) -> int:
        captured["target"] = args.target
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd", fake_cmd)

    toml_path = make_toml(tmp_path)

    rc = main(["--config", str(toml_path), "upgrade"])

    assert rc == 0
    assert captured["target"] is None


def test_run_upgrade_should_emit_service_upgraded_when_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_use_unpinned_pip_install_when_no_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_exit_1_with_no_event_when_pip_install_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_emit_rolled_back_event_when_smoke_peek_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke peek fails → pip --force-reinstall <from> → sanity smoke → start →
    emit rolled_back."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    version_calls = [0]

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "migrate" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            version_calls[0] += 1
            # 1st --version (smoke for new): version reads as 0.1.99
            # 2nd --version (sanity smoke for restored): reads as from_version
            if version_calls[0] == 1:
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


def test_run_upgrade_should_rollback_when_smoke_version_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke --version fails (not just peek) → still triggers rollback."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    version_calls = [0]

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            version_calls[0] += 1
            if version_calls[0] == 1:  # first --version after pip → fails
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


def test_run_upgrade_rollback_pip_should_force_reinstall_from_version_when_smoke_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    pip_calls = []
    version_calls = [0]

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            pip_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            version_calls[0] += 1
            if version_calls[0] == 1:
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


def test_run_upgrade_should_exit_2_with_rollback_failed_event_when_rollback_pip_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_emit_rollback_failed_when_sanity_smoke_fails_after_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_fail_without_calling_pip_when_api_stop_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def _raise_stop(_wd):
        raise RuntimeError("systemctl failed")

    monkeypatch.setattr(api, "stop", _raise_stop)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

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


def test_run_upgrade_should_fail_without_calling_stop_when_target_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml = make_toml(tmp_path)
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


def test_pip_env_flags_should_return_empty_when_in_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from agent_runner.cli import upgrade_cmd

    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "_base")  # prefix != base => venv

    assert upgrade_cmd._pip_env_flags() == []


def test_pip_env_flags_should_return_user_and_break_system_when_non_venv_user_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import site
    import sys

    import agent_runner
    from agent_runner.cli import upgrade_cmd

    monkeypatch.setattr(sys, "base_prefix", sys.prefix)  # not a venv
    monkeypatch.setattr(site, "getusersitepackages", lambda: "/home/u/.local/lib/py/site-packages")
    monkeypatch.setattr(
        agent_runner, "__file__", "/home/u/.local/lib/py/site-packages/agent_runner/__init__.py"
    )

    assert upgrade_cmd._pip_env_flags() == ["--user", "--break-system-packages"]


def test_pip_env_flags_should_return_break_system_only_when_non_venv_system_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import site
    import sys

    import agent_runner
    from agent_runner.cli import upgrade_cmd

    monkeypatch.setattr(sys, "base_prefix", sys.prefix)  # not a venv
    monkeypatch.setattr(site, "getusersitepackages", lambda: "/home/u/.local/lib/py/site-packages")
    monkeypatch.setattr(
        agent_runner,
        "__file__",
        "/usr/lib/python3/dist-packages/agent_runner/__init__.py",
    )

    assert upgrade_cmd._pip_env_flags() == ["--break-system-packages"]


def test_pip_install_should_retry_with_break_system_packages_when_externally_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from agent_runner.cli import upgrade_cmd

    monkeypatch.setattr(upgrade_cmd, "_pip_env_flags", lambda: ["--break-system-packages"])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--break-system-packages" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="error: externally-managed-environment"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = upgrade_cmd._pip_install("cli-agent-runner==9.9.9")

    assert r.returncode == 0
    assert len(calls) == 2
    assert "--break-system-packages" in calls[1]


def test_pip_install_should_not_retry_when_failure_is_not_externally_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from agent_runner.cli import upgrade_cmd

    monkeypatch.setattr(upgrade_cmd, "_pip_env_flags", lambda: ["--break-system-packages"])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="ERROR: no such package"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = upgrade_cmd._pip_install("cli-agent-runner==9.9.9")

    assert r.returncode == 1
    assert len(calls) == 1  # no retry on a non-PEP668 failure


def _fake_run_factory(call_log, version="0.1.99"):
    import subprocess

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {version}\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    return fake_run


def test_run_upgrade_should_stay_package_only_without_crash_when_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from agent_runner import api
    from agent_runner.cli import upgrade_cmd

    started = []
    monkeypatch.setattr(api, "start", lambda _wd: started.append(_wd))
    monkeypatch.setattr(api, "stop", lambda _wd: started.append(("stop", _wd)))
    call_log = []
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(call_log))

    rc = upgrade_cmd._run_upgrade(None, target="0.1.99", cfg_path=Path("agent-runner.toml"))

    assert rc == 0
    assert started == []  # service never touched
    assert any(_is_pip_call(c) for c in call_log)
    assert any("--version" in c for c in call_log)


def test_run_upgrade_should_stay_package_only_without_start_when_service_mode_is_pid_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    started = []
    monkeypatch.setattr(api, "start", lambda _wd: started.append(_wd))
    monkeypatch.setattr(api, "stop", lambda _wd: started.append(("stop", _wd)))
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.PID_FILE)
    call_log = []
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(call_log))

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)

    assert rc == 0
    assert started == []  # neither stop nor start called in package-only mode

    payloads = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    pkg = [p for p in payloads if p["event"] == "package_upgraded"]
    assert len(pkg) == 1
    assert pkg[0]["restart_deferred"] is True
    assert pkg[0]["to_version"] == "0.1.99"
    assert not [p for p in payloads if p["event"] == "service_upgraded"]


def test_run_upgrade_should_stay_package_only_when_no_restart_flag_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    started = []
    monkeypatch.setattr(api, "start", lambda _wd: started.append(_wd))
    monkeypatch.setattr(api, "stop", lambda _wd: started.append(("stop", _wd)))
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)
    monkeypatch.setattr(subprocess, "run", _fake_run_factory([]))

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path, no_restart=True)

    assert rc == 0
    assert started == []  # --no-restart forces package-only even on user mode


def test_run_upgrade_should_rollback_without_start_when_package_only_smoke_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    started = []
    monkeypatch.setattr(api, "start", lambda _wd: started.append(_wd))
    monkeypatch.setattr(api, "stop", lambda _wd: started.append(("stop", _wd)))
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.NONE)

    pip_calls = []

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            pip_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:  # smoke always fails
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = load_config(toml_path)

    rc = upgrade_cmd._run_upgrade(cfg, target="0.1.99", cfg_path=toml_path)

    assert rc == 1  # smoke failed, rolled back
    assert started == []  # service never started/stopped
    assert any("--force-reinstall" in c for c in pip_calls)  # pip-level rollback happened


def _config_arg(cmd) -> str | None:
    """The --config path in a `-m agent_runner.cli --config <p> <sub>` invocation."""
    if "--config" in cmd:
        return cmd[cmd.index("--config") + 1]
    return None


def test_run_upgrade_should_restore_config_when_rollback_follows_manual_migrate_remainder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """New-binary migrate hits a manual remainder (rc=1) after mutating the file →
    upgrade rolls back to from_version AND the config is restored to pre-migrate."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    original = toml_path.read_text(encoding="utf-8")

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "migrate" in cmd:
            Path(_config_arg(cmd)).write_text(original + "\nmutated_by_migrate = true\n")
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="  MANUAL:  fix command by hand", stderr=""
            )
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="9.9.9", cfg_path=toml_path)

    assert rc == 1  # rolled back
    assert toml_path.read_text(encoding="utf-8") == original  # config restored

    payloads = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert any(p["event"] == "service_upgrade_rolled_back" for p in payloads)


def test_run_upgrade_should_restore_config_when_rollback_follows_migrate_success_then_smoke_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """New-binary migrate succeeds (rewrites the file to the new schema), but the
    peek smoke AFTER it fails → rollback still must restore the pre-migrate
    config, not just leave the new-schema file for the reinstalled old binary."""
    import json
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    original = toml_path.read_text(encoding="utf-8")

    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    version_calls = [0]

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "migrate" in cmd:
            # migrate SUCCEEDS but rewrites the file to the new schema
            Path(_config_arg(cmd)).write_text(original + "\nmigrated_field = true\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            version_calls[0] += 1
            # 1st --version (smoke for new): 9.9.9. 2nd (sanity smoke restored): from_version.
            if version_calls[0] == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="agent-runner 9.9.9\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        if "peek" in cmd:
            # peek FAILS after a successful migrate — triggers rollback
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="KeyError: 'phases'"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(toml_path)
    rc = upgrade_cmd._run_upgrade(cfg, target="9.9.9", cfg_path=toml_path)

    assert rc == 1  # rolled back
    assert toml_path.read_text(encoding="utf-8") == original  # migrate's rewrite undone

    payloads = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert any(p["event"] == "service_upgrade_rolled_back" for p in payloads)


def test_run_upgrade_should_call_migrate_before_peek_smoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from agent_runner import __version__, api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    monkeypatch.setattr(api, "stop", lambda _wd: None)
    monkeypatch.setattr(api, "start", lambda _wd: None)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    order = []

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "migrate" in cmd:
            order.append("migrate")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"agent-runner {__version__}\n", stderr=""
            )
        if "peek" in cmd:
            order.append("peek")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = load_config(toml_path)

    upgrade_cmd._run_upgrade(cfg, target="9.9.9", cfg_path=toml_path)

    assert order == ["migrate", "peek"]


def test_run_upgrade_should_emit_upgrade_start_failed_when_api_start_raises_after_smoke_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Smoke passes but api.start raises → emit upgrade_start_failed (not a
    mislabeled rollback), exit 2, and print a systemctl remedy."""
    import json
    import subprocess

    from agent_runner import api
    from agent_runner.api_types import ServiceMode
    from agent_runner.cli import upgrade_cmd
    from agent_runner.config import load_config

    toml_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(api, "stop", lambda _wd: None)

    def boom(_wd):
        raise RuntimeError("unit failed to start")

    monkeypatch.setattr(api, "start", boom)
    monkeypatch.setattr(api, "detect_service_mode", lambda *a, **k: ServiceMode.SYSTEMD_USER)

    def fake_run(cmd, **kwargs):
        if _is_pip_call(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="agent-runner 9.9.9\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = load_config(toml_path)

    rc = upgrade_cmd._run_upgrade(cfg, target="9.9.9", cfg_path=toml_path)

    assert rc == 2

    payloads = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    kinds = {p["event"] for p in payloads}
    assert "upgrade_start_failed" in kinds
    assert "service_upgrade_rollback_failed" not in kinds
    # remedy goes to stderr via cli.common.fail()
    assert "systemctl --user" in capsys.readouterr().err
