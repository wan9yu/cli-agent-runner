from __future__ import annotations

from pathlib import Path

import pytest


def _make_args(**kwargs):
    """Build a minimal args namespace for serve command."""
    from argparse import Namespace

    return Namespace(
        config=kwargs.pop("config", Path("/tmp/cfg.toml")),
        max_rounds=kwargs.pop("max_rounds", None),
        once=kwargs.pop("once", False),
        **kwargs,
    )


def test_given_cli_max_rounds_when_resolved_then_overrides_config():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    # CLI flag set to 3, config has 10 → effective is 3
    result = _resolve_max_rounds(cli_value=3, config_value=10)
    assert result == 3


def test_given_no_cli_max_rounds_when_resolved_then_uses_config():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    result = _resolve_max_rounds(cli_value=None, config_value=10)
    assert result == 10


def test_given_neither_set_when_resolved_then_none():
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    result = _resolve_max_rounds(cli_value=None, config_value=None)
    assert result is None


@pytest.mark.parametrize("invalid", [0, -1, -100])
def test_given_invalid_cli_max_rounds_when_resolved_then_raises(invalid: int):
    from agent_runner.cli.serve_cmd import _resolve_max_rounds

    with pytest.raises(ValueError, match=r"--max-rounds must be positive"):
        _resolve_max_rounds(cli_value=invalid, config_value=None)


def test_given_argparse_when_max_rounds_passed_then_parsed_as_int():
    """argparse type=int parses --max-rounds correctly."""
    from agent_runner.cli import _build_parser

    args = _build_parser().parse_args(["serve", "--config", "/tmp/x.toml", "--max-rounds", "5"])
    assert args.max_rounds == 5
