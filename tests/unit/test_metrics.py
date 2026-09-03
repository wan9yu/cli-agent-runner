from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_runner.metrics import _read_psi, collect, log_metrics, sample


def test_given_collect_when_called_then_returns_dict_with_mem_and_disk_fields(
    tmp_path: Path,
) -> None:
    m = collect(tmp_path)
    assert "mem_total_mb" in m
    assert "mem_available_mb" in m
    assert "mem_used_pct" in m
    assert "disk_total_gb" in m
    assert "disk_free_gb" in m
    assert "disk_used_pct" in m
    assert "load_1m" in m
    assert m["mem_total_mb"] > 0
    assert m["disk_total_gb"] > 0


def test_given_sample_when_called_then_returns_lean_pressure_signal_keys() -> None:
    s = sample()
    assert set(s) == {
        "mem_available_mb",
        "mem_free_mb",
        "swap_sout",
        "psi_some_avg10",
        "psi_full_avg10",
    }
    assert s["mem_available_mb"] > 0
    assert s["mem_free_mb"] >= 0
    assert s["swap_sout"] >= 0


def test_given_sample_when_called_then_never_shells_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """sample() must be lean: no subprocess, unlike collect()'s pgrep call."""

    def _boom(*args, **kwargs):
        raise AssertionError("sample() must not call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    sample()  # must not raise


def test_given_no_psi_file_when_read_psi_then_returns_none(tmp_path: Path) -> None:
    assert _read_psi(tmp_path / "nonexistent") is None


def test_given_psi_file_when_read_psi_then_parses_avg10(tmp_path: Path) -> None:
    psi_path = tmp_path / "memory"
    psi_path.write_text(
        "some avg10=12.34 avg60=5.00 avg300=1.00 total=999\n"
        "full avg10=1.50 avg60=0.50 avg300=0.10 total=111\n"
    )
    assert _read_psi(psi_path) == (12.34, 1.50)


def test_given_psi_file_missing_full_line_when_read_psi_then_full_defaults_zero(
    tmp_path: Path,
) -> None:
    psi_path = tmp_path / "memory"
    psi_path.write_text("some avg10=3.00 avg60=1.00 avg300=0.00 total=5\n")
    assert _read_psi(psi_path) == (3.00, 0.0)


def test_given_collect_when_called_then_merges_sample_fields(tmp_path: Path) -> None:
    m = collect(tmp_path)
    assert "swap_sout" in m
    assert "psi_some_avg10" in m
    assert "mem_free_mb" in m


def test_given_log_metrics_when_called_then_appends_jsonl_with_event_field(
    tmp_log_dir: Path,
) -> None:
    log_metrics(tmp_log_dir, event="periodic", round_num=5)
    files = list(tmp_log_dir.glob("metrics-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]
    assert rows[0]["event"] == "periodic"
    assert rows[0]["round_num"] == 5
    assert "mem_available_mb" in rows[0]
    assert "disk_free_gb" in rows[0]


def test_given_log_metrics_in_different_months_when_called_then_separate_files(
    tmp_log_dir: Path,
    monkeypatch,
) -> None:
    """Same monthly-naming convention as events.jsonl."""
    from datetime import UTC, datetime

    from agent_runner.clock import SYSTEM_CLOCK

    monkeypatch.setattr(SYSTEM_CLOCK, "now_utc", lambda: datetime(2026, 4, 30, 23, 0, tzinfo=UTC))
    log_metrics(tmp_log_dir, event="periodic")
    assert (tmp_log_dir / "metrics-2026-04.jsonl").exists()
