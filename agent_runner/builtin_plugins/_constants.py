"""Shared constants and tail-scan helpers for built-in CLI plugins.

Extracted to single source of truth so every round-log tail-scanner
(plugin JSONL parsers and monitor text detectors) uses the same window
size, raw-text caps, transient-error back-off defaults, and the same
HTTP-status → classification ladder.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

_TAIL_LINES: int = 200
"""Number of log lines to scan from the end of a round log.

The round log is merged stdout+stderr. JSONL consumers window via
``json_tail`` (chatter filtered before windowing); plain-text consumers
(monitor oauth/network detectors) window the raw tail — keep this generous
so a stderr burst cannot evict what they scan for."""


def json_tail(f: TextIO, maxlen: int = _TAIL_LINES) -> deque[str]:
    """Last ``maxlen`` JSON-looking lines of a merged round log.

    Non-JSON chatter (stderr text) is filtered BEFORE windowing, so a burst
    of any size cannot evict the terminal JSONL event from the window.
    """
    return deque((ln for ln in f if ln.lstrip()[:1] in "{["), maxlen=maxlen)


def json_events(log_path: Path) -> Iterator[dict]:
    """JSON objects in a round log's tail window, in file order.

    One reader for every plugin's parse loop: window via ``json_tail``, then
    per line strip / skip blank / ``json.loads`` / drop anything that is not an
    object. Non-JSON lines are expected — the round log merges stdout+stderr,
    and every CLI writes some plain text there.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = json_tail(f)
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


_RAW_CAP: int = 200
"""Maximum length for ``raw`` field in transient_error_detected payload."""

# Default back-off durations (seconds) for non-precise transient classifications.
# rate_limit_account uses exact resetsAt epoch from claude; not in this table.
_BACK_OFF_DEFAULTS: dict[str, int] = {
    "rate_limit_model": 60,
    "api_transient_5xx": 60,
    "api_timeout": 30,
}

# 5xx codes treated as transient (retry-worthy server errors per RFC 9110):
# 500=unexpected, 502=bad gateway, 503=unavailable, 504=gateway timeout,
# 529=overloaded (Anthropic's non-RFC code emitted during sustained capacity
# issues; treated as transient per Anthropic SDK behavior).
# Excluded: 501 (not implemented = permanent), 505 (HTTP version mismatch).
_5XX_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504, 529})


def classify_transient_status(status: Any) -> str | None:
    """Map an HTTP status from a CLI's error record to a transient bucket.

    The one ladder every plugin classifier delegates to, so a status is
    bucketed identically no matter which CLI reported it.

    ``None`` means 'not transient — do not back off'. Notably 401 (auth) and
    404 (unknown model) land here: both are permanent until an operator fixes
    configuration, which is oauth/config territory rather than something a
    back-off would clear. Non-integer or missing statuses are ``None`` too;
    a CLI that reports no status gets no classification.
    """
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    if status == 429:
        return "rate_limit_model"
    if status in _5XX_STATUSES:
        return "api_transient_5xx"
    if status == 408:
        return "api_timeout"
    return None


_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "rate_limit_account",
        "rate_limit_model",
        "api_transient_5xx",
        "api_timeout",
    }
)
"""Canonical set of transient-error classifications.

rate_limit_account uses server-provided resetsAt (excluded from
_BACK_OFF_DEFAULTS table); others use defaults from that table.
"""

_EXP_CAP: int = 5
"""Maximum exponent for transient-error consecutive backoff: 2^5 = 32×.

Beyond this, the multiplier plateaus. Combined with _ABSOLUTE_CAP_S, this
prevents runaway wait times during sustained outages (max wait = 30min).
"""

_ABSOLUTE_CAP_S: int = 1800
"""Absolute upper bound on supervisor-applied transient back-off (30 min).

Applies after exp multiplier — even if base × 2^5 exceeds this, the wait
is clipped here. Defends against an indefinitely-stuck supervisor.
"""
