"""Tests for HTTP progress endpoint."""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen


@contextmanager
def _serving(log_dir: Path, narrative_file: Path | None = None, port: int = 0):
    """Start http_progress in a background thread on an ephemeral port.

    Yields the actual port bound. Stops the server on context exit.
    """
    from agent_runner.http_progress import build_server

    server = build_server(log_dir, narrative_file, port=port)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            try:
                with socket.create_connection(("127.0.0.1", actual_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        yield actual_port
    finally:
        server.shutdown()
        server.server_close()


def test_get_root_should_return_html_with_all_sections_when_server_running(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with _serving(log_dir) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")

        assert resp.status == 200
        body = resp.read().decode("utf-8")
        for marker in ("round", "narrative", "events", "self-termin"):
            assert marker.lower() in body.lower(), f"missing section: {marker}"


def test_get_api_state_should_return_json_with_expected_keys_when_server_running(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with _serving(log_dir) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/api/state")

        assert resp.status == 200
        data = json.loads(resp.read())
        expected_keys = (
            "round_state",
            "narrative",
            "recent_events",
            "round_log_tail",
            "self_terminated",
        )
        for key in expected_keys:
            assert key in data, f"missing key: {key}"


def test_get_root_should_render_with_hints_when_log_dir_empty(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with _serving(log_dir) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")

        assert resp.status == 200


def test_get_root_should_render_narrative_content_when_narrative_file_given(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    narrative = tmp_path / "narrative.md"
    narrative.write_text("Round 1: hypothesis X covered.\nRound 2: moving to Y.\n")

    with _serving(log_dir, narrative_file=narrative) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")

        body = resp.read().decode("utf-8")
        assert "hypothesis X covered" in body


def test_rate_limit_state_should_stay_throttled_when_sibling_agent_recovered(
    tmp_path: Path,
) -> None:
    """``_rate_limit_state`` must not report "not throttled" when the newest transient
    event is a sibling agent's recovered while another agent is still throttled — it
    must share the same max-reset fallback ``api.peek`` already has, via
    ``effective_throttle_view``."""
    from agent_runner.http_progress import _rate_limit_state

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    future = int(time.time() + 3600)
    past = int(time.time() - 60)
    rows = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "classification": "rate_limit_account",
            "round_num": 7,
        },
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:01:00Z",
            "agent": "gemini",
            "reset_at_epoch": past,
            "classification": "rate_limit_model",
            "round_num": 8,
        },
        {
            "event": "transient_error_recovered",
            "ts": "2026-05-16T00:02:00Z",
            "agent": "gemini",
            "throttled_for_s": 60,
            "classification": "rate_limit_model",
        },
    ]
    (log_dir / "events-2026-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    rl = _rate_limit_state(log_dir)

    assert rl is not None  # not dropped to null — claude is still throttled
    assert rl["limit_type"] == "rate_limit_account"
    assert rl["since_round"] == 7


def test_serve_http_progress_should_exit_1_when_port_in_use(tmp_path: Path, capsys) -> None:
    from agent_runner.http_progress import serve_http_progress

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    blocked_port = blocker.getsockname()[1]

    try:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        rc = serve_http_progress(log_dir, None, port=blocked_port)

        assert rc == 1
        err = capsys.readouterr().err
        assert f"port {blocked_port}" in err
    finally:
        blocker.close()
