"""Unit tests for the _is_fresh_eyes_round helper in cli/serve_cmd.py."""

from __future__ import annotations


def test_given_every_n_none_when_check_then_false():
    from agent_runner.cli.serve_cmd import _is_fresh_eyes_round

    assert _is_fresh_eyes_round(round_num=5, every_n=None) is False


def test_given_round_num_zero_when_check_then_false_even_if_every_n_set():
    from agent_runner.cli.serve_cmd import _is_fresh_eyes_round

    assert _is_fresh_eyes_round(round_num=0, every_n=1) is False


def test_given_round_num_is_multiple_of_every_n_when_check_then_true():
    from agent_runner.cli.serve_cmd import _is_fresh_eyes_round

    assert _is_fresh_eyes_round(round_num=50, every_n=50) is True
    assert _is_fresh_eyes_round(round_num=100, every_n=50) is True


def test_given_round_num_is_not_multiple_when_check_then_false():
    from agent_runner.cli.serve_cmd import _is_fresh_eyes_round

    assert _is_fresh_eyes_round(round_num=51, every_n=50) is False
    assert _is_fresh_eyes_round(round_num=49, every_n=50) is False
