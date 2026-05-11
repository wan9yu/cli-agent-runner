from __future__ import annotations

import json
from pathlib import Path

from agent_runner.metrics import collect, log_metrics


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

    import agent_runner.metrics as m

    class FakeDt:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 4, 30, 23, 0, tzinfo=UTC)

    monkeypatch.setattr(m, "datetime", FakeDt)
    log_metrics(tmp_log_dir, event="periodic")
    assert (tmp_log_dir / "metrics-2026-04.jsonl").exists()
