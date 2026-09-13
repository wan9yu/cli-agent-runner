from __future__ import annotations

import sys

import pytest

from agent_runner._sandbox_probe import TIER_B_PROTOCOLS, probe_sandbox_capability


def test_probe_should_never_raise_and_report_a_tier():
    probe = probe_sandbox_capability()

    assert probe.achieved_tier in ("landlock+seccomp", "landlock", "seccomp", "unconfined")


def test_probe_should_name_a_reason_when_unconfined():
    probe = probe_sandbox_capability()

    assert probe.achieved_tier != "unconfined" or probe.unconfined_reason is not None


def test_tier_b_protocols_should_exclude_tier_a_observation_hooks():
    assert TIER_B_PROTOCOLS == ("spawn_hooks", "dirty_handlers")


def _sandbox_bindings_available() -> bool:
    if sys.platform != "linux":
        return False
    try:
        import py_landlock  # noqa: F401
        import pyseccomp  # noqa: F401

        return True
    except ImportError:
        return False


# The two tests below are platform-mirror assertions, collected and RUN on
# every host (never a plain skip -- see tests/invariants/test_kill_tests_not_skipped.py
# for why a skip here would be the exact failure mode this file exists to
# avoid: a capable CI runner silently never proving the achieved tier). Off
# their intended platform they strict-xfail instead: still executed, expected
# to fail there, and `strict=True` turns a surprise pass back into a failure
# if the gating condition ever drifts out of sync with reality.


@pytest.mark.xfail(
    sys.platform == "linux",
    reason="non-linux-only: on Linux the probe never reports the 'non-linux' reason",
    strict=True,
    raises=AssertionError,
)
def test_probe_should_report_unconfined_with_non_linux_reason_when_off_linux():
    probe = probe_sandbox_capability()

    assert probe.achieved_tier == "unconfined"
    assert probe.unconfined_reason == "non-linux"
    assert probe.landlock_abi is None
    assert probe.seccomp is False


@pytest.mark.xfail(
    not _sandbox_bindings_available(),
    reason=(
        "Landlock/seccomp capability requires Linux with the [sandbox] extra "
        "installed; this host cannot achieve a confined tier"
    ),
    strict=True,
    raises=AssertionError,
)
def test_probe_should_achieve_a_confined_tier_when_bindings_available():
    probe = probe_sandbox_capability()

    assert probe.achieved_tier != "unconfined"
