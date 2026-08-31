from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent_runner import monitor


def test_one_crashing_builtin_detector_emits_detector_error_and_others_run(tmp_path: Path) -> None:
    boom = RuntimeError("detector blew up")
    with patch.object(monitor, "detect_timeout_rate", side_effect=boom):
        alerts = monitor.run_all_detectors(
            events=[], metrics=[{"disk_used_pct": 96.0}], log_tails={}, log_dir=tmp_path
        )
    # disk_critical still fired despite timeout_rate crashing
    assert any(a.detector == "disk_critical" for a in alerts)
    line = (tmp_path / next(p.name for p in tmp_path.glob("events-*.jsonl"))).read_text()
    assert '"event": "detector_error"' in line
    assert '"detector": "timeout_rate"' in line


def test_run_all_detectors_without_log_dir_swallows_crash(tmp_path: Path) -> None:
    with patch.object(monitor, "detect_orphan_chain", side_effect=ValueError("x")):
        alerts = monitor.run_all_detectors(events=[], metrics=[], log_tails=[])
    assert alerts == []  # no emit, no raise
