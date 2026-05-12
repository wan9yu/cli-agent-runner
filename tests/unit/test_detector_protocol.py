"""Tests for the public Detector Protocol surface."""

from __future__ import annotations

from agent_runner.api_types import Detector


class _FakeDetector:
    name = "fake_kind"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        return None


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
