"""Tests for agent_runner package entry_points loading."""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch


def test_given_failing_plugin_when_loader_runs_then_warns_but_does_not_crash() -> None:
    """A plugin import error must not crash the supervisor."""
    from agent_runner import _load_event_kind_plugins

    bad_ep = MagicMock()
    bad_ep.name = "bad-plugin"
    bad_ep.load.side_effect = RuntimeError("simulated import failure")

    with patch("importlib.metadata.entry_points", return_value=[bad_ep]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_event_kind_plugins()
        assert any("bad-plugin" in str(w.message) for w in caught), (
            f"expected warning mentioning 'bad-plugin'; got {[str(w.message) for w in caught]}"
        )


def test_given_good_plugin_when_loader_runs_then_ep_load_called() -> None:
    """The loader calls ``ep.load()`` for each entry_points entry."""
    from agent_runner import _load_event_kind_plugins

    good_ep = MagicMock()
    good_ep.name = "good-plugin"
    good_ep.load = MagicMock(return_value=None)

    with patch("importlib.metadata.entry_points", return_value=[good_ep]):
        _load_event_kind_plugins()

    good_ep.load.assert_called_once()


def test_given_loader_called_then_uses_correct_entry_points_group() -> None:
    """The loader queries the ``agent_runner.event_kinds`` group, not arbitrary."""
    from agent_runner import _load_event_kind_plugins

    with patch("importlib.metadata.entry_points", return_value=[]) as mock_ep:
        _load_event_kind_plugins()
    mock_ep.assert_called_once_with(group="agent_runner.event_kinds")


def test_given_failing_hook_plugin_when_loader_runs_then_warns_but_does_not_crash() -> None:
    """Hook plugin import failures degrade to UserWarning, same as event_kinds."""
    from agent_runner import _load_hook_plugins

    bad_ep = MagicMock()
    bad_ep.name = "bad-hook"
    bad_ep.load.side_effect = RuntimeError("simulated hook import failure")

    with patch("importlib.metadata.entry_points", return_value=[bad_ep]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_hook_plugins()
        assert any("bad-hook" in str(w.message) for w in caught), (
            f"expected warning mentioning 'bad-hook'; got {[str(w.message) for w in caught]}"
        )


def test_given_loader_called_then_uses_three_hook_groups() -> None:
    """The hook loader queries exactly the three documented entry_points groups."""
    from agent_runner import _load_hook_plugins

    call_groups: list[str] = []

    def fake_eps(group):
        call_groups.append(group)
        return []

    with patch("importlib.metadata.entry_points", side_effect=fake_eps):
        _load_hook_plugins()

    assert sorted(call_groups) == sorted(
        [
            "agent_runner.pre_round_hooks",
            "agent_runner.context_enrichers",
            "agent_runner.post_round_hooks",
        ]
    )


def test_given_failing_detector_plugin_when_loader_runs_then_warns_but_does_not_crash() -> None:
    from agent_runner import _load_detector_plugins

    bad_ep = MagicMock()
    bad_ep.name = "bad-detector"
    bad_ep.load.side_effect = RuntimeError("simulated detector import failure")

    with patch("importlib.metadata.entry_points", return_value=[bad_ep]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_detector_plugins()
        assert any("bad-detector" in str(w.message) for w in caught)


def test_given_detector_loader_called_then_uses_correct_group() -> None:
    from agent_runner import _load_detector_plugins

    with patch("importlib.metadata.entry_points", return_value=[]) as mock_ep:
        _load_detector_plugins()
    mock_ep.assert_called_once_with(group="agent_runner.detectors")
