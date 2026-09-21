from __future__ import annotations

from tests.e2e.growth_child import _CHUNK_BYTES, _PACE_S


def test_growth_child_should_pace_eight_mib_every_two_point_five_seconds_when_invoked() -> None:
    assert _CHUNK_BYTES == 8 * 1024 * 1024
    assert _PACE_S == 2.5
