from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner import api
from agent_runner.config import load_config


def _seed(
    tmp_git_repo: Path, *, disk_pct: float, mem_avail_mb: int, mem_free_mb: int = 3000
) -> None:
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text("")
    (log_dir / "metrics-2026-05.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-12T10:00:00.000Z",
                "event": "round_end",
                "mem_total_mb": 8000,
                "mem_available_mb": mem_avail_mb,
                "mem_free_mb": mem_free_mb,
                "disk_used_pct": disk_pct,
                "disk_free_gb": 1.0,
            }
        )
        + "\n"
    )
    (log_dir / "status.json").write_text(json.dumps({"round_num": 0, "running": False}))


def test_given_seeded_disk_critical_when_poll_once_then_emits_auto_stop_alert(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed(tmp_git_repo, disk_pct=98.0, mem_avail_mb=4000)
    alerts = api._poll_once(tmp_git_repo)
    assert any(a.detector == "disk_critical" and a.auto_action == "stop_service" for a in alerts)


def test_given_seeded_mem_pressure_when_poll_once_then_emits_warning(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    # mem_free_mb=5 (low) alongside mem_available below default threshold (200) is
    # a genuine combined-low signal -- a bare low mem_available_mb alone (the old
    # dumb gate) would now report mem_signal_unavailable instead, not mem_pressure.
    _seed(tmp_git_repo, disk_pct=50.0, mem_avail_mb=100, mem_free_mb=5)
    alerts = api._poll_once(tmp_git_repo)
    assert any(a.detector == "mem_pressure" for a in alerts)


def test_given_bare_low_mem_available_with_no_other_signal_when_poll_once_then_reports_unavailable(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field-bug shape: MemAvailable alone reads low but nothing else is known
    about this host (no MemFree, no swap history, no PSI) -- the honest detector
    must say it cannot tell, not silently trust MemAvailable (the original bug)."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text("")
    (log_dir / "metrics-2026-05.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-12T10:00:00.000Z",
                "event": "round_end",
                "mem_total_mb": 8000,
                "mem_available_mb": 100,
                "disk_used_pct": 50.0,
                "disk_free_gb": 1.0,
            }
        )
        + "\n"
    )
    (log_dir / "status.json").write_text(json.dumps({"round_num": 0, "running": False}))
    alerts = api._poll_once(tmp_git_repo)
    assert any(a.detector == "mem_signal_unavailable" for a in alerts)
    assert not any(a.detector == "mem_pressure" for a in alerts)


def test_given_cache_poor_host_field_bug_shape_when_poll_once_then_fires_pressure_and_gate_inert(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Gateway field-bug shape: MemAvailable stays high (inflated ~15x on a
    cache-poor host) while swap-out climbs across two samples. mem_pressure must
    fire from the swap-out-rate signal, AND the fail-loud self-check must say the
    configured mem_avail_min_mb gate is inert on this host -- the two independent
    defects the field report named."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text("")
    lines = [
        {
            "ts": "2026-05-12T10:00:00.000Z",
            "event": "round_end",
            "mem_total_mb": 462,
            # Above the default mem_avail_min_mb (200) -- the inflated-MemAvailable
            # shape from the field bug, where the gate never gets low enough to fire.
            "mem_available_mb": 250,
            "mem_free_mb": 8,
            "swap_sout": 1_000_000,
            "disk_used_pct": 50.0,
            "disk_free_gb": 1.0,
        },
        {
            "ts": "2026-05-12T10:05:00.000Z",
            "event": "round_end",
            "mem_total_mb": 462,
            "mem_available_mb": 250,
            "mem_free_mb": 5,
            "swap_sout": 9_000_000,
            "disk_used_pct": 50.0,
            "disk_free_gb": 1.0,
        },
    ]
    (log_dir / "metrics-2026-05.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n"
    )
    (log_dir / "status.json").write_text(json.dumps({"round_num": 0, "running": False}))
    alerts = api._poll_once(tmp_git_repo)
    kinds = {a.detector for a in alerts}
    assert "mem_pressure" in kinds
    assert "mem_pressure_gate_inert" in kinds
