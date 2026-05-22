from __future__ import annotations

import json
from pathlib import Path

from agent_runner.monitor import (
    LocalSource,
    assemble_project_state,
    load_round_log_tails,
    parse_events_from_jsonl_files,
    run_all_detectors,
)


def _seed(tmp_log_dir: Path) -> None:
    (tmp_log_dir / "events-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:00.000Z","event":"round_start","round_num":1}\n'
        '{"ts":"2026-05-12T10:00:01.000Z","event":"agent_exit","round_num":1,'
        '"exit_code":0,"duration_s":42.0,"timed_out":false}\n'
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","round_num":1}\n'
    )
    (tmp_log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end",'
        '"disk_used_pct":50.0,"mem_available_mb":4000}\n'
    )
    (tmp_log_dir / "status.json").write_text(
        json.dumps(
            {
                "round_num": 1,
                "running": False,
                "last_exit_code": 0,
            }
        )
    )
    rounds = tmp_log_dir / "rounds"
    rounds.mkdir(exist_ok=True)
    (rounds / "R1-20260512T100000.log").write_text("agent ran fine\n")


def test_given_local_source_when_parsed_then_returns_event_list(tmp_log_dir: Path) -> None:
    _seed(tmp_log_dir)
    src = LocalSource(log_dir=tmp_log_dir)
    events = parse_events_from_jsonl_files(src.events_files())
    assert len(events) == 3
    assert events[0]["event"] == "round_start"


def test_given_round_logs_when_loaded_then_returns_dict_keyed_by_round_num(
    tmp_log_dir: Path,
) -> None:
    _seed(tmp_log_dir)
    src = LocalSource(log_dir=tmp_log_dir)
    tails = load_round_log_tails(src.rounds_dir(), tail_lines=10)
    assert 1 in tails
    assert "agent ran fine" in tails[1]


def test_given_seeded_state_when_assembled_then_returns_project_state(
    tmp_log_dir: Path,
) -> None:
    _seed(tmp_log_dir)
    src = LocalSource(log_dir=tmp_log_dir)
    state = assemble_project_state(src, project="myproj")
    assert state.project == "myproj"
    assert state.status["round_num"] == 1
    assert state.system.disk_used_pct == 50.0


def test_given_clean_history_when_run_all_detectors_then_no_alerts(
    tmp_log_dir: Path,
) -> None:
    _seed(tmp_log_dir)
    src = LocalSource(log_dir=tmp_log_dir)
    events = parse_events_from_jsonl_files(src.events_files())
    metrics = parse_events_from_jsonl_files(src.metrics_files())
    log_tails = load_round_log_tails(src.rounds_dir(), tail_lines=50)
    alerts = run_all_detectors(
        events=events,
        metrics=metrics,
        log_tails=log_tails,
        round_timeout_s=1800,
        supervisor_stale_threshold_s=0,  # disable: seeded events use a fixed old timestamp
    )
    assert alerts == []


def test_given_disk_98_pct_when_run_all_detectors_then_critical_with_auto_stop(
    tmp_log_dir: Path,
) -> None:
    _seed(tmp_log_dir)
    (tmp_log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:00.000Z","event":"round_end",'
        '"disk_used_pct":98.0,"mem_available_mb":4000}\n'
    )
    src = LocalSource(log_dir=tmp_log_dir)
    metrics = parse_events_from_jsonl_files(src.metrics_files())
    alerts = run_all_detectors(
        events=[],
        metrics=metrics,
        log_tails={},
        round_timeout_s=1800,
    )
    crit = [a for a in alerts if a.detector == "disk_critical"]
    assert len(crit) == 1
    assert crit[0].auto_action == "stop_service"
