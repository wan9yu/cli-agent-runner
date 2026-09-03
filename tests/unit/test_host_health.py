"""host_health — pure interpretation of the memory-pressure signal ladder.

Fixtures stand in for real ``/proc``+psutil reads (``metrics.sample()`` supplies
the real thing); this module only tests the ladder logic: PSI -> swap-out-rate
delta -> combined-low -> unavailable, plus the fail-loud inert-gate self-check
(the corrected condition: fires when a cache-poor-valid signal shows real
pressure WHILE mem_available_mb is still >= the configured threshold — NOT on
bare MemAvailable-vs-MemFree divergence, which is true on every healthy
warm-cache host).
"""

from __future__ import annotations

from agent_runner import host_health
from agent_runner.config import MonitorHostHealthConfig


def _cfg(mem_avail_min_mb: int = 40) -> MonitorHostHealthConfig:
    return MonitorHostHealthConfig(mem_avail_min_mb=mem_avail_min_mb)


def test_pressure_from_sout_delta_when_memavailable_high() -> None:
    """swap actively climbing is real pressure even while MemAvailable reads high
    (the cache-poor host's own defect — this is the signal that should NOT be
    fooled by it)."""
    prev = {"swap_sout": 1000, "mem_free_mb": 8, "mem_available_mb": 150, "psi_some_avg10": None}
    cur = {"swap_sout": 9000, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}
    assert host_health.memory_pressure(cur, prev, _cfg()) is not None


def test_no_swap_delta_below_noise_floor_reports_no_pressure() -> None:
    """A trivial (<= one page) sout delta is a benign one-time idle-page swap,
    not active paging — must NOT be reported as pressure."""
    prev = {"swap_sout": 1000, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    cur = {"swap_sout": 1500, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    assert host_health.memory_pressure(cur, prev, _cfg()) is None


def test_psi_readable_and_quiet_reports_no_pressure_even_with_swap_climbing() -> None:
    """PSI is the strongest signal — when it's readable and quiet, trust it and
    do not fall through to the swap tier."""
    prev = {"swap_sout": 1000, "mem_free_mb": 8, "mem_available_mb": 150, "psi_some_avg10": 0.0}
    cur = {"swap_sout": 9000, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": 0.0}
    assert host_health.memory_pressure(cur, prev, _cfg()) is None


def test_psi_real_pressure_detected_regardless_of_memavailable() -> None:
    cur = {"swap_sout": 100, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": 12.0}
    prev = {"swap_sout": 100}
    pressure = host_health.memory_pressure(cur, prev, _cfg())
    assert pressure is not None
    assert pressure.signal == "psi"


def test_combined_low_fires_when_memfree_and_memavailable_both_low() -> None:
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    prev = {"swap_sout": None}
    pressure = host_health.memory_pressure(cur, prev, _cfg(40))
    assert pressure is not None
    assert pressure.signal == "combined_low"


def test_quiet_swap_delta_still_falls_through_to_combined_low() -> None:
    """A swapless/zram-less host's sout never moves (delta always ~0) -- that
    must NOT be read as "healthy, stop checking": it's simply no evidence
    from that tier, and combined-low must still catch real pressure."""
    prev = {"swap_sout": 100, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    cur = {"swap_sout": 100, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    pressure = host_health.memory_pressure(cur, prev, _cfg(40))
    assert pressure is not None
    assert pressure.signal == "combined_low"


def test_memfree_low_alone_does_not_fire_combined_low_on_cache_heavy_host() -> None:
    """A cache-heavy healthy host's MemFree is always low -- never gate on it alone."""
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 6000, "psi_some_avg10": None}
    prev = {"swap_sout": None}
    assert host_health.memory_pressure(cur, prev, _cfg(40)) is None


def test_no_signal_reports_unavailable_not_memavailable_gate() -> None:
    s = {"swap_sout": None, "mem_free_mb": None, "mem_available_mb": 150, "psi_some_avg10": None}
    assert host_health.memory_pressure(s, s, _cfg()) is None
    assert host_health.signal_available(s, s) is False


def test_signal_available_true_when_any_tier_has_data() -> None:
    cur = {"swap_sout": 100, "mem_free_mb": None, "mem_available_mb": 150, "psi_some_avg10": None}
    prev = {"swap_sout": 90}
    assert host_health.signal_available(cur, prev) is True


def test_inert_gate_flagged_when_pressure_but_avail_above_threshold() -> None:
    cur = {"swap_sout": 9000, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}
    assert host_health.configured_gate_inert(cur, {"swap_sout": 1000}, _cfg(40)) is True


def test_inert_gate_not_flagged_on_healthy_warm_cache_host() -> None:
    """MemAvailable >> MemFree alone is true on every healthy warm-cache host --
    must NOT be flagged as inert."""
    cur = {"swap_sout": 100, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": 0.0}
    assert host_health.configured_gate_inert(cur, {"swap_sout": 100}, _cfg(40)) is False


def test_inert_gate_not_flagged_when_gate_would_actually_fire() -> None:
    """combined_low pressure always coincides with mem_available < threshold --
    the gate is reachable, not inert."""
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    assert host_health.configured_gate_inert(cur, {"swap_sout": None}, _cfg(40)) is False
