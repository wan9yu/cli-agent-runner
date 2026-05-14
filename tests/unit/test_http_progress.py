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


def test_given_running_server_when_get_root_then_html_with_sections(tmp_path: Path) -> None:
    """GET / returns HTML 200 with all 5 sections rendered."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with _serving(log_dir) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "<html" in body.lower()
        for marker in ("round", "narrative", "events", "self-termin"):
            assert marker.lower() in body.lower(), f"missing section: {marker}"


def test_given_running_server_when_get_api_state_then_json(tmp_path: Path) -> None:
    """GET /api/state returns valid JSON with expected keys."""
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


def test_given_no_log_dir_state_when_get_then_renders_with_hints(tmp_path: Path) -> None:
    """Empty log_dir → page renders with hints, no crash."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with _serving(log_dir) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")
        assert resp.status == 200


def test_given_narrative_file_when_get_then_rendered(tmp_path: Path) -> None:
    """Narrative file content appears in the rendered page."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    narrative = tmp_path / "narrative.md"
    narrative.write_text("Round 1: hypothesis X covered.\nRound 2: moving to Y.\n")

    with _serving(log_dir, narrative_file=narrative) as port:
        resp = urlopen(f"http://127.0.0.1:{port}/")
        body = resp.read().decode("utf-8")
        assert "hypothesis X covered" in body


def test_given_port_in_use_when_serve_http_progress_then_exit_1(tmp_path: Path, capsys) -> None:
    """If the requested port is in use, serve_http_progress returns 1 with structured stderr."""
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
