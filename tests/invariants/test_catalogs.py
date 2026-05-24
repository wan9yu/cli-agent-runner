"""Defense catalog and dependency invariants."""

from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
REPO = PKG.parent


def test_given_defenses_catalog_when_loaded_then_each_entry_has_required_fields() -> None:
    from agent_runner.config import (
        AgentConfig,
        Config,
        PromptConfig,
        RuntimeConfig,
        VcsConfig,
    )
    from agent_runner.defenses import catalog

    cfg = Config(
        agent=AgentConfig(command=["x"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=Path("/tmp"), log_dir=Path("/tmp/logs")),
        prompt=PromptConfig(file=Path("/tmp/p.md"), inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
    cat = catalog(cfg)
    assert len(cat) == 11
    for d in cat:
        assert d.name and isinstance(d.name, str)
        assert d.current_state in {"active", "degraded", "off"}


def test_given_defenses_invariant_paths_when_resolved_then_all_exist_or_none() -> None:
    from agent_runner.config import (
        AgentConfig,
        Config,
        PromptConfig,
        RuntimeConfig,
        VcsConfig,
    )
    from agent_runner.defenses import catalog

    cfg = Config(
        agent=AgentConfig(command=["x"], prompt_arg_template=["-p", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=Path("/tmp"), log_dir=Path("/tmp/logs")),
        prompt=PromptConfig(file=Path("/tmp/p.md"), inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
    for d in catalog(cfg):
        if d.guarded_by is None:
            continue
        full = REPO / d.guarded_by
        assert full.exists(), f"{d.name} references missing test {d.guarded_by}"


def test_given_codebase_when_scanned_then_no_paramiko_or_fabric_runtime_deps() -> None:
    """ssh stays subprocess-based; paramiko/fabric only allowed in tests/e2e."""
    offenders: list[tuple[str, str]] = []
    for f in PKG.rglob("*.py"):
        text = f.read_text()
        for forbidden in ("paramiko", "fabric"):
            if f"import {forbidden}" in text or f"from {forbidden}" in text:
                offenders.append((str(f.relative_to(REPO)), forbidden))
    assert offenders == [], f"runtime modules import paramiko/fabric: {offenders}"


def test_given_emit_calls_for_monitor_events_when_scanned_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: monitor's two new event kinds must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "monitor_alert_emitted" in KNOWN_EVENT_KINDS
    assert "monitor_auto_stop_triggered" in KNOWN_EVENT_KINDS


def test_given_monitor_started_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: monitor_started must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "monitor_started" in KNOWN_EVENT_KINDS


def test_given_monitor_remote_blip_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: monitor_remote_blip must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "monitor_remote_blip" in KNOWN_EVENT_KINDS


def test_given_monitor_remote_giveup_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: monitor_remote_giveup must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "monitor_remote_giveup" in KNOWN_EVENT_KINDS


def test_given_agent_network_blip_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: agent_network_blip must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "agent_network_blip" in KNOWN_EVENT_KINDS


def test_given_prompt_overwritten_kind_when_registered_then_in_known_event_kinds() -> None:
    """prompt_overwritten built-in event kind."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "prompt_overwritten" in KNOWN_EVENT_KINDS


def test_given_upgraded_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: service_upgraded must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "service_upgraded" in KNOWN_EVENT_KINDS


def test_given_rolled_back_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: service_upgrade_rolled_back must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "service_upgrade_rolled_back" in KNOWN_EVENT_KINDS


def test_given_rollback_failed_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: service_upgrade_rollback_failed must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "service_upgrade_rollback_failed" in KNOWN_EVENT_KINDS


def test_given_serve_startup_hook_failed_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: serve_startup_hook_failed must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "serve_startup_hook_failed" in KNOWN_EVENT_KINDS


def test_given_agent_self_terminated_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: agent_self_terminated must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "agent_self_terminated" in KNOWN_EVENT_KINDS


def test_given_dirty_commit_failed_kind_when_registered_then_in_known_event_kinds() -> None:
    """Belt-and-suspenders: dirty_commit_failed must be in KNOWN_EVENT_KINDS."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "dirty_commit_failed" in KNOWN_EVENT_KINDS


def test_given_max_rounds_reached_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, MAX_ROUNDS_REACHED

    assert MAX_ROUNDS_REACHED == "max_rounds_reached"
    assert MAX_ROUNDS_REACHED in _BUILTIN_KINDS


def test_given_stop_file_detected_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, STOP_FILE_DETECTED

    assert STOP_FILE_DETECTED == "stop_file_detected"
    assert STOP_FILE_DETECTED in _BUILTIN_KINDS


def test_given_round_substrate_before_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, ROUND_SUBSTRATE_BEFORE

    assert ROUND_SUBSTRATE_BEFORE == "round_substrate_before"
    assert ROUND_SUBSTRATE_BEFORE in _BUILTIN_KINDS


def test_given_round_substrate_after_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, ROUND_SUBSTRATE_AFTER

    assert ROUND_SUBSTRATE_AFTER == "round_substrate_after"
    assert ROUND_SUBSTRATE_AFTER in _BUILTIN_KINDS


def test_given_fresh_eyes_round_triggered_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, FRESH_EYES_ROUND_TRIGGERED

    assert FRESH_EYES_ROUND_TRIGGERED == "fresh_eyes_round_triggered"
    assert FRESH_EYES_ROUND_TRIGGERED in _BUILTIN_KINDS


def test_given_transient_error_detected_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, TRANSIENT_ERROR_DETECTED

    assert TRANSIENT_ERROR_DETECTED == "transient_error_detected"
    assert TRANSIENT_ERROR_DETECTED in _BUILTIN_KINDS


def test_given_transient_error_recovered_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, TRANSIENT_ERROR_RECOVERED

    assert TRANSIENT_ERROR_RECOVERED == "transient_error_recovered"
    assert TRANSIENT_ERROR_RECOVERED in _BUILTIN_KINDS


def test_given_transient_error_backoff_capped_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, TRANSIENT_ERROR_BACKOFF_CAPPED

    assert TRANSIENT_ERROR_BACKOFF_CAPPED == "transient_error_backoff_capped"
    assert TRANSIENT_ERROR_BACKOFF_CAPPED in _BUILTIN_KINDS


def test_given_agent_usage_recorded_constant_when_exported_then_matches_kind():
    from agent_runner.events import _BUILTIN_KINDS, AGENT_USAGE_RECORDED

    assert AGENT_USAGE_RECORDED == "agent_usage_recorded"
    assert AGENT_USAGE_RECORDED in _BUILTIN_KINDS


def test_given_package_upgraded_kind_when_registered_then_in_known_event_kinds() -> None:
    from agent_runner.events import _BUILTIN_KINDS, PACKAGE_UPGRADED

    assert PACKAGE_UPGRADED == "package_upgraded"
    assert PACKAGE_UPGRADED in _BUILTIN_KINDS


def test_given_round_grace_extended_kind_when_registered_then_in_known_event_kinds() -> None:
    from agent_runner.events import _BUILTIN_KINDS, ROUND_GRACE_EXTENDED

    assert ROUND_GRACE_EXTENDED == "round_grace_extended"
    assert ROUND_GRACE_EXTENDED in _BUILTIN_KINDS
