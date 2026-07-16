from agent_runner.agent_runtime import RunResult
from agent_runner.runner import _scan_round_log_for_network_blip
from tests._test_helpers import read_events_for_current_month  # existing helper

# NOTE: "temporary failure in name resolution" does NOT match NETWORK_PATTERNS
# in agent_runner/monitor.py (verified) — the regex has no "resolution"/"failure"
# alternative. "DNS lookup failed" does match via the bare "dns" alternative, so
# it is used here to keep the RED/GREEN honest.
DNS_LOG_LINE = "... DNS lookup failed ...\n"


def _blip_kinds(log_dir):
    try:
        events = read_events_for_current_month(log_dir)
    except FileNotFoundError:
        return []
    return [e.get("event") for e in events]


def test_signal_death_with_network_string_is_not_a_blip(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = tmp_path / "round.log"
    log_path.write_text(DNS_LOG_LINE)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=RunResult(exit_code=143, duration_s=1.0, timed_out=False, pid=1),
        round_num=1,
        phase=None,
    )
    assert "agent_network_blip" not in _blip_kinds(log_dir)


def test_plain_error_with_network_string_still_blips(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = tmp_path / "round.log"
    log_path.write_text(DNS_LOG_LINE)
    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=RunResult(exit_code=1, duration_s=1.0, timed_out=False, pid=1),
        round_num=1,
        phase=None,
    )
    assert "agent_network_blip" in _blip_kinds(log_dir)
