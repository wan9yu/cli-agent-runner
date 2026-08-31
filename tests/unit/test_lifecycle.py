from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from agent_runner.api_types import ServiceMode
from agent_runner.lifecycle import (
    PIDFile,
    detect_service_mode,
    pid_alive,
    send_signal_to_pid,
)


def test_given_pid_file_when_written_and_read_then_round_trips(tmp_path: Path) -> None:
    pf = PIDFile(tmp_path / "p.pid")
    pf.write(12345)
    assert pf.read() == 12345
    pf.unlink()
    assert pf.read() is None


def test_given_pid_file_when_unlink_missing_then_silent(tmp_path: Path) -> None:
    PIDFile(tmp_path / "absent.pid").unlink()  # must not raise


def test_given_pid_file_when_corrupt_then_read_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.pid"
    p.write_text("not-a-pid")
    assert PIDFile(p).read() is None


def test_live_pid_with_matching_token_reads_back(tmp_path: Path) -> None:
    pf = PIDFile(tmp_path / "p.pid")
    pf.write(os.getpid())  # captures this process's real start-time
    assert pf.read() == os.getpid()  # token matches the live process


def test_recycled_pid_with_mismatched_token_reads_none(tmp_path: Path) -> None:
    """Same PID, different process (OS recycled it after a crash) → the start-time
    token won't match, so read() reports it as not-running (no stray signal)."""
    import json

    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": os.getpid(), "create_time": 1.0}))  # bogus old start-time
    assert PIDFile(p).read() is None


def test_legacy_bare_int_reads_unverified(tmp_path: Path) -> None:
    """A pre-0.2.11 bare-int file has no token → returned unverified (back-compat)."""
    p = tmp_path / "p.pid"
    p.write_text(str(os.getpid()))
    assert PIDFile(p).read() == os.getpid()


def test_legacy_bare_bool_true_reads_none(tmp_path: Path) -> None:
    """json `true` is an int subclass — must NOT slip through as pid 1."""
    p = tmp_path / "p.pid"
    p.write_text("true")
    assert PIDFile(p).read() is None


def test_legacy_bare_pid_one_reads_none(tmp_path: Path) -> None:
    """pid 1 is init — never a serve process we started."""
    p = tmp_path / "p.pid"
    p.write_text("1")
    assert PIDFile(p).read() is None


def test_dict_bool_pid_reads_none(tmp_path: Path) -> None:
    import json

    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": True}))
    assert PIDFile(p).read() is None


def test_dict_pid_one_reads_none(tmp_path: Path) -> None:
    import json

    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": 1}))
    assert PIDFile(p).read() is None


def test_given_running_pid_when_pid_alive_then_returns_true() -> None:
    assert pid_alive(os.getpid()) is True


def test_given_dead_pid_when_pid_alive_then_returns_false() -> None:
    p = subprocess.Popen(["true"])
    p.wait()
    time.sleep(0.05)
    assert pid_alive(p.pid) is False


def test_given_invalid_pid_when_send_signal_then_returns_false() -> None:
    assert send_signal_to_pid(999999999, signal.SIGTERM) is False


def test_given_no_systemd_unit_no_pidfile_when_detect_then_returns_none(tmp_path: Path) -> None:
    assert detect_service_mode("nonexistent-project", log_dir=tmp_path) == ServiceMode.NONE


def test_given_pid_file_present_when_detect_then_returns_pid_file(tmp_path: Path) -> None:
    (tmp_path / "serve.pid").write_text(str(os.getpid()))
    assert detect_service_mode("p", log_dir=tmp_path) == ServiceMode.PID_FILE


def test_given_systemd_unit_file_when_detect_then_returns_systemd_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_systemd = tmp_path / "systemd-user"
    fake_systemd.mkdir()
    (fake_systemd / "agent-runner@myproj.service").write_text("[Unit]\n")
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir",
        lambda: fake_systemd,
    )
    assert detect_service_mode("myproj", log_dir=tmp_path) == ServiceMode.SYSTEMD_USER
