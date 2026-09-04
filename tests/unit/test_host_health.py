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


_50MB = 50 * 1024 * 1024


def test_pressure_from_sout_delta_when_memavailable_high() -> None:
    """swap actively climbing is real pressure even while MemAvailable reads high
    (the cache-poor host's own defect — this is the signal that should NOT be
    fooled by it)."""
    prev = {"swap_sout": 0, "mem_free_mb": 8, "mem_available_mb": 150, "psi_some_avg10": None}
    cur = {"swap_sout": _50MB, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}
    assert host_health.memory_pressure(cur, prev, _cfg()) is not None


def test_given_realistic_field_episode_when_sustained_swap_and_low_memfree_then_critical() -> None:
    """The real field-bug shape: PSI unreadable, MemAvailable inflated at 82MB
    (well above mem_avail_min_mb=40 -- combined-low genuinely cannot fire),
    MemFree critically low (~5MB on a small host), and swap_out climbing by a
    REALISTIC per-interval amount (300MB -- not a gigabyte-scale spike a slow
    SD-card/USB swap device could never produce in one interval). Critical is
    gated on MemFree, not on the swap-out RATE's magnitude, so this fires
    regardless of the swap device's actual throughput."""
    field_swap_delta = 300 * 1024 * 1024  # realistic per-interval rate, not a GiB spike
    prev = {"swap_sout": 0, "mem_free_mb": 5, "mem_available_mb": 82, "psi_some_avg10": None}
    cur = {
        "swap_sout": field_swap_delta,
        "mem_free_mb": 5,
        "mem_available_mb": 82,
        "psi_some_avg10": None,
    }
    pressure = host_health.memory_pressure(cur, prev, _cfg(40))
    assert pressure is not None
    assert pressure.severity == "critical"
    assert pressure.signal == "swap_out_rate"


def test_real_swap_delta_with_ample_memfree_stays_warning_not_critical() -> None:
    """A real, above-floor swap-out delta with MemFree comfortably high (not
    the field host's dying state) stays a warning -- critical is gated on
    MemFree being critically low, not on the delta's magnitude alone."""
    prev = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 150, "psi_some_avg10": None}
    cur = {"swap_sout": _50MB, "mem_free_mb": 200, "mem_available_mb": 150, "psi_some_avg10": None}
    pressure = host_health.memory_pressure(cur, prev, _cfg())
    assert pressure is not None
    assert pressure.severity == "warning"


def test_no_swap_delta_below_noise_floor_reports_no_pressure() -> None:
    """A trivial sout delta is a benign one-time idle-page swap, not active
    paging — must NOT be reported as pressure."""
    prev = {"swap_sout": 1000, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    cur = {"swap_sout": 1500, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    assert host_health.memory_pressure(cur, prev, _cfg()) is None


def test_moderate_few_mb_swap_churn_below_raised_floor_reports_no_pressure() -> None:
    """A few MB of swap movement between successive samples (round-boundary
    startup/idle churn) is common and meaningless -- the noise floor was
    raised from one page to tens of MB precisely so a PSI-off host does not
    spuriously defer/resume every round on benign churn like this."""
    prev = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    cur = {
        "swap_sout": 5 * 1024 * 1024,
        "mem_free_mb": 200,
        "mem_available_mb": 6000,
        "psi_some_avg10": None,
    }
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
    cur = {"swap_sout": _50MB, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}
    assert host_health.configured_gate_inert(cur, {"swap_sout": 0}, _cfg(40)) is True


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


def test_memory_pressure_uses_cfg_swap_floor() -> None:
    """swap_sout_noise_floor_mb must be read from cfg, not the deleted module
    constant -- a 9 MiB delta clears an 8 MiB floor but not a 32 MiB one.
    PSI is unreadable (None) here so the ladder falls through to tier 2 --
    a readable-and-quiet PSI (e.g. 0.0) would short-circuit at tier 1 and
    never reach the swap-out floor this test exercises."""
    from agent_runner.config import MonitorHostHealthConfig

    cfg = MonitorHostHealthConfig(
        swap_sout_noise_floor_mb=8, mem_free_low_mb=16, mem_avail_min_mb=200
    )
    prev = {
        "swap_sout": 0,
        "mem_free_mb": 500,
        "mem_available_mb": 500,
        "psi_some_avg10": None,
        "psi_full_avg10": None,
    }
    cur = {
        "swap_sout": 9 * 1024 * 1024,
        "mem_free_mb": 500,
        "mem_available_mb": 500,
        "psi_some_avg10": None,
        "psi_full_avg10": None,
    }
    p = host_health.memory_pressure(cur, prev, cfg)  # 9 MiB delta > 8 MiB floor
    assert p is not None and p.signal == "swap_out_rate"

    cfg2 = MonitorHostHealthConfig(
        swap_sout_noise_floor_mb=32, mem_free_low_mb=16, mem_avail_min_mb=200
    )
    assert host_health.memory_pressure(cur, prev, cfg2) is None  # 9 MiB < 32 MiB floor: no warning
