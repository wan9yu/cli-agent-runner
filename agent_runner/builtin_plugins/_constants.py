"""Shared constants for built-in CLI plugins and round-log tail readers.

Extracted to single source of truth so every round-log tail-scanner
(plugin JSONL parsers and monitor text detectors) uses the same window
size, raw-text caps, and transient-error back-off defaults.
"""

from __future__ import annotations

from collections import deque
from typing import TextIO

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
