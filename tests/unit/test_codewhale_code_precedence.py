from __future__ import annotations

from agent_runner.builtin_plugins.codewhale import _classify_codewhale_error


def test_symbolic_string_code_does_not_mask_numeric_status_code() -> None:
    # A truthy symbolic `code` and a numeric 429 `status_code`: must classify as 429.
    ev = {"type": "error", "code": "rate_limit_exceeded", "status_code": 429}
    assert _classify_codewhale_error(ev) == "rate_limit_model"


def test_numeric_code_still_wins_when_present() -> None:
    assert _classify_codewhale_error({"type": "error", "code": 503}) == "api_transient_5xx"


def test_free_text_only_maps_to_nothing() -> None:
    assert _classify_codewhale_error({"type": "error", "error": "auth failed"}) is None
