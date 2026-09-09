"""0.2.18 T1c: the host swap-total probe -- the plausibility ceiling shared by
the cgroup-defer swap-plausibility guard and the startup swap-cap advisory
(see tests/unit/test_cgroup_probe.py's "swap-plausibility guard" section)."""

from __future__ import annotations

from agent_runner import metrics


def test_swap_total_bytes_should_return_psutil_swap_total_value(monkeypatch):
    class S:
        total = 1234

    monkeypatch.setattr(metrics.psutil, "swap_memory", lambda: S())

    assert metrics.swap_total_bytes() == 1234


def test_swap_total_bytes_should_match_real_psutil_swap_total():
    import psutil

    assert metrics.swap_total_bytes() == psutil.swap_memory().total
