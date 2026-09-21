from __future__ import annotations

from agent_runner.builtin_plugins.codewhale import _classify_codewhale_error


def test_classify_codewhale_error_should_use_status_code_when_code_is_symbolic_string() -> None:
    # A truthy symbolic `code` and a numeric 429 `status_code`: must classify as 429.

    ev = {"type": "error", "code": "rate_limit_exceeded", "status_code": 429}

    assert _classify_codewhale_error(ev) == "rate_limit_model"


def test_classify_codewhale_error_should_use_numeric_code_when_present() -> None:

    actual = _classify_codewhale_error({"type": "error", "code": 503})

    expected = "api_transient_5xx"

    assert actual == expected


def test_classify_codewhale_error_should_return_none_when_only_free_text_present() -> None:

    actual = _classify_codewhale_error({"type": "error", "error": "auth failed"})

    expected = None

    assert actual is expected
