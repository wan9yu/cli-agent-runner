from __future__ import annotations

import pytest

from agent_runner.cli import _build_parser


def test_latest_should_be_kept_as_string_when_parsed() -> None:

    args = _build_parser().parse_args(["peek", "--round", "latest"])

    actual = args.round

    assert actual == "latest"


def test_int_string_should_convert_to_int_when_parsed() -> None:

    args = _build_parser().parse_args(["peek", "--round", "42"])

    actual = args.round

    assert actual == 42


def test_garbage_round_should_exit_argparse_when_parsed(capsys) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["peek", "--round", "abc"])

    err = capsys.readouterr().err

    assert "abc" in err
