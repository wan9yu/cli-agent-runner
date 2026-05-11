from __future__ import annotations

import pytest

from agent_runner.cli import _build_parser


def test_given_latest_when_parsed_then_kept_as_string() -> None:
    args = _build_parser().parse_args(["peek", "--round", "latest"])
    assert args.round == "latest"


def test_given_int_string_when_parsed_then_converts_to_int() -> None:
    args = _build_parser().parse_args(["peek", "--round", "42"])
    assert args.round == 42


def test_given_garbage_round_when_parsed_then_argparse_exits(capsys) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["peek", "--round", "abc"])
    err = capsys.readouterr().err
    assert "abc" in err
