from __future__ import annotations

import pytest


def test_resolve_max_rounds_should_prefer_cli_value_when_config_also_set():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    # CLI flag set to 3, config has 10 → effective is 3
    result = _resolve_max_rounds(cli_value=3, config_value=10)

    assert result == 3


def test_resolve_max_rounds_should_use_config_value_when_cli_not_set():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    result = _resolve_max_rounds(cli_value=None, config_value=10)

    assert result == 10


def test_resolve_max_rounds_should_return_none_when_neither_cli_nor_config_set():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    result = _resolve_max_rounds(cli_value=None, config_value=None)

    assert result is None


@pytest.mark.parametrize("invalid", [0, -1, -100])
def test_resolve_max_rounds_should_raise_when_cli_value_invalid(invalid: int):
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    with pytest.raises(ValueError, match=r"--max-rounds must be positive"):
        _resolve_max_rounds(cli_value=invalid, config_value=None)


def test_max_rounds_arg_should_parse_as_int_when_passed_via_cli():
    """argparse type=int parses --max-rounds correctly."""
    from agent_runner.cli import _build_parser

    args = _build_parser().parse_args(["serve", "--config", "/tmp/x.toml", "--max-rounds", "5"])

    assert args.max_rounds == 5
