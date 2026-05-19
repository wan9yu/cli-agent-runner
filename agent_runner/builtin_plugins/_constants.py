"""Shared constants for built-in CLI plugins (claude, gemini, ...).

Extracted to single source of truth so all plugin tail-scanners use the
same window size, raw-text caps, and transient-error back-off defaults.
"""

from __future__ import annotations

_TAIL_LINES: int = 50
"""Number of log lines to scan from the end of round-N.log."""

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
