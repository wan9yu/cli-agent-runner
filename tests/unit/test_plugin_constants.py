"""Unit tests for the shared plugin tail-scan helpers in ``_constants``.

These are the two pieces every built-in CLI plugin now delegates to, so they
are pinned once here instead of in each plugin's own test file.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from agent_runner.builtin_plugins._constants import (
    _BACK_OFF_DEFAULTS,
    _TAIL_LINES,
    classify_transient_status,
    json_events,
    json_tail,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "rate_limit_model"),
        (500, "api_transient_5xx"),
        (502, "api_transient_5xx"),
        (503, "api_transient_5xx"),
        (504, "api_transient_5xx"),
        (529, "api_transient_5xx"),  # Anthropic's non-RFC overloaded code
        (408, "api_timeout"),
        # permanent until an operator fixes config — not back-off territory
        (401, None),
        (404, None),
        (400, None),
        (501, None),  # not implemented: a 5xx that is NOT transient
        (None, None),
        ("429", None),  # a string status is not a status
        ({"code": 429}, None),  # unhashable garbage must not raise
    ],
)
def test_classify_transient_status_should_map_to_expected_bucket(status, expected) -> None:
    assert classify_transient_status(status) == expected
    if expected is not None:
        # every non-None bucket must have a back-off duration to apply
        assert expected in _BACK_OFF_DEFAULTS


def test_mixed_lines_should_yield_only_dicts_when_json_events(tmp_path: Path) -> None:
    """The round log merges stdout+stderr: plain text, blank lines and
    JSON arrays all appear, and only JSON objects are events."""
    log = tmp_path / "R1-test.log"
    log.write_text(
        "node:internal/process/warning: ExperimentalWarning\n"
        '{"type":"first","n":1}\n'
        "\n"
        "not json at all\n"
        '["array","line"]\n'
        '{"type":"second","n":2}\n'
        '{"broken": \n',
        encoding="utf-8",
    )

    assert list(json_events(log)) == [{"type": "first", "n": 1}, {"type": "second", "n": 2}]


def test_blank_line_should_not_be_kept_when_json_tail() -> None:
    """A blank line's first char is '' — substring membership ('' in '{[') wrongly
    kept it; tuple membership ('' in ('{','[')) rejects it."""
    buf = io.StringIO('{"a":1}\n' + "\n" * 5 + "   \n")

    assert list(json_tail(buf)) == ['{"a":1}\n']


def test_terminal_json_record_should_not_be_evicted_when_followed_by_blank_flood(
    tmp_path,
) -> None:
    """Terminal JSON record followed by more blank lines than the window: blanks
    must not fill the deque and evict the record."""
    log = tmp_path / "R7-test.log"
    log.write_text('{"type":"terminal","n":9}\n' + "\n" * (_TAIL_LINES + 100), encoding="utf-8")

    assert list(json_events(log)) == [{"type": "terminal", "n": 9}]
