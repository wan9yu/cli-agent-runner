"""HTTP progress endpoint — local-only browser visibility for `agent-runner serve`.

Stdlib-only (``http.server.HTTPServer`` + ``BaseHTTPRequestHandler``). No JS/CSS
framework, no auth, binds 127.0.0.1 only. 5-second meta-refresh on the HTML
page; ``GET /api/state`` returns the same underlying data as JSON.

Reads existing on-disk state: ``status.json``, ``events-*.jsonl``,
``round-current.log``, ``.agent-done``, ``narrative_file``. No writes.
"""

from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def serve_http_progress(log_dir: Path, narrative_file: Path | None, *, port: int) -> int:
    """Start the HTTP server and serve forever. Returns 1 on port-in-use error."""
    try:
        server = build_server(log_dir, narrative_file, port=port)
    except OSError as e:
        print(
            f"agent-runner: port {port} in use ({e}); try --port <other>",
            file=sys.stderr,
        )
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_server(log_dir: Path, narrative_file: Path | None, *, port: int) -> ThreadingHTTPServer:
    """Build and return an HTTPServer bound to 127.0.0.1:port.

    Factored out for testability — tests bind to port=0 and read .server_address.
    """
    resolved_narrative = (
        narrative_file if narrative_file is not None else (log_dir / "narrative.md")
    )

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — stdlib API requires this name
            if self.path == "/":
                self._send_html()
            elif self.path == "/api/state":
                self._send_json()
            else:
                self.send_error(404, "not found")

        def log_message(self, _format, *args):  # stdlib override; params unused
            # Suppress default access-log spam to stderr
            return

        def _send_html(self):
            state = _build_state(log_dir, resolved_narrative)
            body = _render_html(state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self):
            state = _build_state(log_dir, resolved_narrative)
            body = json.dumps(state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def _build_state(log_dir: Path, narrative_file: Path) -> dict[str, Any]:
    """Collect all on-disk state for both HTML and JSON renderings."""
    return {
        "round_state": _round_state(log_dir),
        "narrative": _read_tail(narrative_file, max_lines=50),
        "recent_events": _recent_events(log_dir, max_count=20),
        "round_log_tail": _read_tail(log_dir / "round-current.log", max_lines=50),
        "self_terminated": _self_terminated_state(log_dir),
        "rate_limit": _rate_limit_state(log_dir),
    }


def _round_state(log_dir: Path) -> dict[str, Any]:
    """Section 1: round-level state from status.json."""
    from agent_runner.context_store import read_status

    s = read_status(log_dir)
    if s is None:
        return {"round_num": 0, "phase": None, "running": False, "last_outcome": None}
    return {
        "round_num": s.round_num,
        "phase": s.current_phase,
        "running": s.running,
        "last_outcome": "ok" if s.last_exit_code == 0 else "failed",
        "last_duration_s": s.last_duration_s,
        "last_completed_at": s.last_completed_at,
    }


def _read_tail(path: Path, *, max_lines: int) -> str:
    """Return last ``max_lines`` lines of ``path``, or empty string if missing."""
    from collections import deque

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=max_lines)
    except (FileNotFoundError, OSError):
        return ""
    return "".join(lines)


def _recent_events(log_dir: Path, *, max_count: int) -> list[dict[str, Any]]:
    """Section 3: last ``max_count`` events from events-*.jsonl files."""
    events: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("events-*.jsonl"))[-3:]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except (FileNotFoundError, OSError):
            continue
    return events[-max_count:]


def _self_terminated_state(log_dir: Path) -> dict[str, Any]:
    """Section 5: ``.agent-done`` flag presence + reason."""
    from agent_runner.api import read_sentinel_content

    reason = read_sentinel_content(log_dir)
    return {"present": reason is not None, "reason": reason}


def _rate_limit_state(log_dir: Path) -> dict[str, Any] | None:
    """Rate limit throttle state — None if not currently throttled."""
    from datetime import UTC, datetime

    from agent_runner._throttle import _check_throttle_state

    throttle = _check_throttle_state(log_dir)
    if throttle is None:
        return None
    iso = datetime.fromtimestamp(throttle.reset_at_epoch, UTC).isoformat()
    return {
        "throttled_until_iso": iso,
        "limit_type": throttle.classification,
        "since_round": throttle.since_round,
    }


def _render_html(state: dict[str, Any]) -> str:
    """Render the 5-section HTML page. No JS/CSS framework — just <pre> and <h2>."""
    rs = state["round_state"]
    selft = state["self_terminated"]
    rl = state.get("rate_limit")
    events_block = "\n".join(_render_event_line(e) for e in state["recent_events"])
    rate_limit_banner = ""
    if rl is not None:
        _style = "background:#fee;color:#900;padding:1em;border:2px solid #900;margin:1em 0;"
        rate_limit_banner = (
            f'<div style="{_style}">'
            f"Throttled until {html.escape(rl['throttled_until_iso'])} "
            f"({html.escape(rl['limit_type'])}, since R{html.escape(str(rl['since_round']))})"
            "</div>"
        )
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>agent-runner progress</title>
</head><body>
<h1>agent-runner progress</h1>
{rate_limit_banner}
<h2>Round state</h2>
<pre>round_num: {html.escape(str(rs["round_num"]))}
phase: {html.escape(str(rs.get("phase")))}
running: {html.escape(str(rs["running"]))}
last_outcome: {html.escape(str(rs.get("last_outcome")))}
last_duration_s: {html.escape(str(rs.get("last_duration_s")))}
</pre>

<h2>Narrative</h2>
<pre>{html.escape(state["narrative"]) or "(no narrative file yet)"}</pre>

<h2>Recent events</h2>
<pre>{events_block or "(no events yet)"}</pre>

<h2>Round log tail</h2>
<pre>{html.escape(state["round_log_tail"]) or "(no round running yet)"}</pre>

<h2>Self-termination</h2>
<pre>{"yes — reason: " + html.escape(selft["reason"] or "") if selft["present"] else "active"}</pre>
</body></html>"""


def _render_event_line(evt: dict[str, Any]) -> str:
    """Format an event dict as one human-readable line for HTML <pre>.

    Intentionally separate from ``api._format_narrate_line``: pad width differs
    (25 vs 20) and this version does not rename ``round_num`` → ``round``.
    """
    ts = evt.get("ts", "")
    time_part = ts[11:23] if len(ts) > 23 else ts
    event_name = evt.get("event", "?")
    kvs = " ".join(f"{k}={v}" for k, v in evt.items() if k not in ("ts", "event"))
    return html.escape(f"[{time_part}] {event_name:<25} {kvs}")
