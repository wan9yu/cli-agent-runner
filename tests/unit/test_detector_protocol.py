"""Tests for the public Detector Protocol surface."""

from __future__ import annotations

import pytest

from agent_runner import monitor
from agent_runner.api_types import Detector


class _FakeDetector:
    name = "fake_kind"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        return None


@pytest.fixture(autouse=True)
def _reset_plugin_detectors():
    """Snapshot + restore plugin detector registry around each test."""
    saved = list(monitor._PLUGIN_DETECTORS)
    monitor._PLUGIN_DETECTORS.clear()
    yield
    monitor._PLUGIN_DETECTORS.clear()
    monitor._PLUGIN_DETECTORS.extend(saved)


def test_given_fake_detector_when_isinstance_checked_then_satisfies_protocol() -> None:
    """Detector is @runtime_checkable; structural typing should accept _FakeDetector."""
    assert isinstance(_FakeDetector(), Detector)


def test_given_object_without_detect_method_when_isinstance_checked_then_does_not_satisfy() -> None:
    class _NotADetector:
        name = "x"
        severity = "warning"
        auto_action = "none"
        # no .detect method

    assert not isinstance(_NotADetector(), Detector)


def test_given_object_without_required_attrs_when_isinstance_checked_then_does_not_satisfy() -> (
    None
):
    class _MissingAttrs:
        def detect(self, state):
            return None

        # missing name / severity / auto_action

    assert not isinstance(_MissingAttrs(), Detector)


def test_given_no_plugin_detectors_when_listed_then_empty() -> None:
    assert monitor.plugin_detectors() == []


def test_given_detector_when_registered_then_visible_in_listing() -> None:
    monitor.register_detector(_FakeDetector())
    assert monitor.plugin_detectors() == ["fake_kind"]


def test_given_duplicate_detector_name_when_registered_then_raises() -> None:
    monitor.register_detector(_FakeDetector())
    with pytest.raises(ValueError, match="already registered"):
        monitor.register_detector(_FakeDetector())
