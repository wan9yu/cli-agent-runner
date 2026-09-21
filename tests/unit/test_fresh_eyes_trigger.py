"""Unit tests for the _is_fresh_eyes_round helper in cli/serve_cmd.py."""

from __future__ import annotations

from agent_runner.cli.serve_cmd import _is_fresh_eyes_round


def test_is_fresh_eyes_round_should_be_false_when_every_n_is_none():

    actual = _is_fresh_eyes_round(round_num=5, every_n=None)

    expected = False

    assert actual is expected


def test_is_fresh_eyes_round_should_be_false_when_round_num_is_zero():

    actual = _is_fresh_eyes_round(round_num=0, every_n=1)

    expected = False

    assert actual is expected


def test_is_fresh_eyes_round_should_be_true_when_round_num_is_multiple_of_every_n():

    assert _is_fresh_eyes_round(round_num=50, every_n=50) is True

    actual = _is_fresh_eyes_round(round_num=100, every_n=50)

    assert actual is True


def test_is_fresh_eyes_round_should_be_false_when_round_num_is_not_multiple_of_every_n():

    assert _is_fresh_eyes_round(round_num=51, every_n=50) is False

    actual = _is_fresh_eyes_round(round_num=49, every_n=50)

    assert actual is False
