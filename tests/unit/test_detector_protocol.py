"""Tests for the public Detector Protocol surface."""

from __future__ import annotations

import pytest

from agent_runner import monitor
from agent_runner.api_types import Detector
from tests._test_helpers import isolating

_reset = isolating(monitor._PLUGIN_DETECTORS)


class _FakeDetector:
    name = "fake_kind"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        return None


def test_fake_detector_should_satisfy_protocol_when_isinstance_checked() -> None:
    """Detector is @runtime_checkable; structural typing should accept _FakeDetector."""
    assert isinstance(_FakeDetector(), Detector)


def test_object_without_detect_method_should_not_satisfy_protocol_when_isinstance_checked() -> None:
    class _NotADetector:
        name = "x"
        severity = "warning"
        auto_action = "none"
        # no .detect method

    assert not isinstance(_NotADetector(), Detector)


def test_object_without_required_attrs_should_not_satisfy_protocol_when_isinstance_checked() -> (
    None
):
    class _MissingAttrs:
        def detect(self, state):
            return None

        # missing name / severity / auto_action

    assert not isinstance(_MissingAttrs(), Detector)


def test_plugin_detectors_should_return_empty_when_none_registered() -> None:
    assert monitor.plugin_detectors() == []


def test_detector_should_be_visible_in_listing_when_registered() -> None:
    monitor.register_detector(_FakeDetector())

    assert monitor.plugin_detectors() == ["fake_kind"]


def test_register_detector_should_raise_when_name_duplicate() -> None:
    monitor.register_detector(_FakeDetector())

    with pytest.raises(ValueError, match="already registered"):
        monitor.register_detector(_FakeDetector())
