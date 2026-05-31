"""Mask secret-bearing tokens in semi-trusted free text before it is persisted
to durable, default-readable event logs (events-*.jsonl).

Single source of truth for secret-shaping. Pure, dependency-free, idempotent.
BEST-EFFORT: applied only to free-text excerpts (transient error output, hook
exception messages/tracebacks), never as the sole control. The grace-kill child
fields avoid this entirely by storing basename+pid, not argv.

Patterns are length/charset-anchored so benign argv (sk-report.md, psql -h db,
'Basic auth disabled') passes through unchanged.
"""

from __future__ import annotations

import re

_MASK = "<redacted>"

_LONG_FLAGS = [
    "--token",
    "--password",
    "--passwd",
    "--api-key",
    "--apikey",
    "--secret",
    "--secret-access-key",
    "--access-key",
    "--auth",
    "--authorization",
    "--client-secret",
    "--aws-session-token",
    "--bearer",
    "--auth-token",
    "--private-key",
]
# (prefix, min-tail-len) — anchored so short filenames don't match.
_PREFIX_RES = [
    r"sk-ant-[A-Za-z0-9\-_]{16,}",
    r"sk-[A-Za-z0-9]{16,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"gho_[A-Za-z0-9]{20,}",
    r"ghs_[A-Za-z0-9]{20,}",
    r"ghu_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[bpars]-[A-Za-z0-9-]{10,}",
    r"xapp-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"glpat-[A-Za-z0-9_\-]{20}",
    r"ya29\.[A-Za-z0-9._\-]{20,}",
    r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}",
    r"npm_[A-Za-z0-9]{20,}",
    r"hf_[A-Za-z0-9]{20,}",
]

_FLAG_RE = re.compile(r"(?i)(" + "|".join(re.escape(f) for f in _LONG_FLAGS) + r")(\s+|=)(\S+)")
# Short HTTP-basic flag `-u user:pass` (curl/wget). Case-SENSITIVE so `-U`
# (psql/pg username) is left alone; a colon is required so `sort -u file` and
# bare `-u username` (no password) are not masked; and the value must NOT be a
# URL (`://`) — a `-u <url>` is left for _URL_USERINFO_RE, which masks only the
# userinfo and preserves the host (e.g. redis://<redacted>@cache:6379/0).
_SHORT_USER_RE = re.compile(r"(?<![\w-])-u(\s+|=)(?!\S*://)(\S+:\S+)")
_HEADER_NAME_RE = re.compile(
    r"(?im)\b(Authorization|Proxy-Authorization|Cookie|Set-Cookie|"
    r"X-Api-Key|X-Auth-Token|X-Amz-Security-Token)(\s*:\s*)([^\r\n]+)"
)
_SCHEME_RE = re.compile(
    r"\b(Bearer|Basic|Token|ApiKey|Digest|Negotiate|NTLM)(\s+)([A-Za-z0-9+/=._\-]{12,})"
)
_URL_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)([^/\s@]+)@")
_URL_QUERY_RE = re.compile(
    r"(?i)([?&#](?:access_token|api_?key|token|auth|secret|sig|signature|"
    r"password|client_secret|x-amz-security-token|x-amz-signature)=)([^&#\s]+)"
)
_ENV_RE = re.compile(
    r"(?i)((?:(?<=\s)|^)[A-Za-z_][A-Za-z0-9_]*"
    r"(?:PASSWORD|PASSWD|PWD|TOKEN|SECRET|API[_-]?KEY|ACCESS[_-]?KEY|CREDENTIAL|PRIVATE[_-]?KEY)"
    r"[A-Za-z0-9_]*)=(\S+)"
)
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_PREFIX_RES) + r")")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)


def redact_secrets(text: str) -> str:
    """Return *text* with secret-bearing tokens replaced by ``<redacted>``."""
    if not text:
        return text
    out = _PEM_RE.sub(_MASK, text)
    out = _ENV_RE.sub(rf"\1={_MASK}", out)
    out = _FLAG_RE.sub(rf"\1\2{_MASK}", out)
    out = _SHORT_USER_RE.sub(rf"-u\1{_MASK}", out)
    out = _HEADER_NAME_RE.sub(rf"\1\2{_MASK}", out)
    out = _SCHEME_RE.sub(rf"\1\2{_MASK}", out)
    out = _URL_USERINFO_RE.sub(rf"\1{_MASK}@", out)
    out = _URL_QUERY_RE.sub(rf"\1{_MASK}", out)
    out = _JWT_RE.sub(_MASK, out)
    out = _PREFIX_RE.sub(_MASK, out)
    return out
