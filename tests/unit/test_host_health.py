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

import dataclasses

from agent_runner import host_health
from agent_runner.config import (
    MonitorHostHealthConfig,
    _HostHealthMemoryConfig,
    _HostHealthPressureConfig,
)


def _cfg(mem_avail_min_mb: int = 40) -> MonitorHostHealthConfig:
    cfg = MonitorHostHealthConfig()
    return dataclasses.replace(
        cfg, memory=dataclasses.replace(cfg.memory, avail_min_mb=mem_avail_min_mb)
    )


_50MB = 50 * 1024 * 1024


def test_memory_pressure_should_detect_pressure_from_sout_delta_when_mem_available_is_high() -> (
    None
):
    """swap actively climbing is real pressure even while MemAvailable reads high
    (the cache-poor host's own defect — this is the signal that should NOT be
    fooled by it)."""
    prev = {"swap_sout": 0, "mem_free_mb": 8, "mem_available_mb": 150, "psi_some_avg10": None}
    cur = {"swap_sout": _50MB, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}

    assert host_health.memory_pressure(cur, prev, _cfg()) is not None


def test_memory_pressure_should_be_critical_when_swap_and_memfree_match_field_episode() -> None:
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


def test_memory_pressure_should_stay_warning_when_swap_delta_real_but_memfree_ample() -> None:
    """A real, above-floor swap-out delta with MemFree comfortably high (not
    the field host's dying state) stays a warning -- critical is gated on
    MemFree being critically low, not on the delta's magnitude alone."""
    prev = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 150, "psi_some_avg10": None}
    cur = {"swap_sout": _50MB, "mem_free_mb": 200, "mem_available_mb": 150, "psi_some_avg10": None}

    pressure = host_health.memory_pressure(cur, prev, _cfg())

    assert pressure is not None
    assert pressure.severity == "warning"


def test_memory_pressure_should_report_no_pressure_when_swap_delta_below_noise_floor() -> None:
    """A trivial sout delta is a benign one-time idle-page swap, not active
    paging — must NOT be reported as pressure."""
    prev = {"swap_sout": 1000, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}
    cur = {"swap_sout": 1500, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": None}

    assert host_health.memory_pressure(cur, prev, _cfg()) is None


def test_memory_pressure_should_report_no_pressure_when_swap_churn_below_raised_noise_floor() -> (
    None
):
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


def test_memory_pressure_should_report_no_pressure_when_psi_quiet_even_if_swap_climbing() -> None:
    """PSI is the strongest signal — when it's readable and quiet, trust it and
    do not fall through to the swap tier."""
    prev = {"swap_sout": 1000, "mem_free_mb": 8, "mem_available_mb": 150, "psi_some_avg10": 0.0}
    cur = {"swap_sout": 9000, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": 0.0}

    assert host_health.memory_pressure(cur, prev, _cfg()) is None


def test_memory_pressure_should_detect_psi_pressure_regardless_of_mem_available() -> None:
    cur = {"swap_sout": 100, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": 12.0}
    prev = {"swap_sout": 100}

    pressure = host_health.memory_pressure(cur, prev, _cfg())

    assert pressure is not None
    assert pressure.signal == "psi"


def test_memory_pressure_should_fire_combined_low_when_memfree_and_memavailable_both_low() -> None:
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    prev = {"swap_sout": None}

    pressure = host_health.memory_pressure(cur, prev, _cfg(40))

    assert pressure is not None
    assert pressure.signal == "combined_low"


def test_memory_pressure_should_fall_through_to_combined_low_when_swap_delta_is_quiet() -> None:
    """A swapless/zram-less host's sout never moves (delta always ~0) -- that
    must NOT be read as "healthy, stop checking": it's simply no evidence
    from that tier, and combined-low must still catch real pressure."""
    prev = {"swap_sout": 100, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}
    cur = {"swap_sout": 100, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}

    pressure = host_health.memory_pressure(cur, prev, _cfg(40))

    assert pressure is not None
    assert pressure.signal == "combined_low"


def test_memory_pressure_should_not_fire_combined_low_when_only_memfree_is_low() -> None:
    """A cache-heavy healthy host's MemFree is always low -- never gate on it alone."""
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 6000, "psi_some_avg10": None}
    prev = {"swap_sout": None}

    assert host_health.memory_pressure(cur, prev, _cfg(40)) is None


def test_memory_pressure_should_return_none_when_no_signal_available() -> None:
    s = {"swap_sout": None, "mem_free_mb": None, "mem_available_mb": 150, "psi_some_avg10": None}

    assert host_health.memory_pressure(s, s, _cfg()) is None


def test_signal_available_should_be_false_when_no_tier_has_data() -> None:
    s = {"swap_sout": None, "mem_free_mb": None, "mem_available_mb": 150, "psi_some_avg10": None}

    assert host_health.signal_available(s, s) is False


def test_signal_available_should_be_true_when_any_tier_has_data() -> None:
    cur = {"swap_sout": 100, "mem_free_mb": None, "mem_available_mb": 150, "psi_some_avg10": None}
    prev = {"swap_sout": 90}

    assert host_health.signal_available(cur, prev) is True


def test_configured_gate_inert_should_be_true_when_pressure_present_but_avail_above_threshold() -> (
    None
):
    cur = {"swap_sout": _50MB, "mem_free_mb": 5, "mem_available_mb": 150, "psi_some_avg10": None}

    assert host_health.configured_gate_inert(cur, {"swap_sout": 0}, _cfg(40)) is True


def test_configured_gate_inert_should_be_false_on_healthy_warm_cache_host() -> None:
    """MemAvailable >> MemFree alone is true on every healthy warm-cache host --
    must NOT be flagged as inert."""
    cur = {"swap_sout": 100, "mem_free_mb": 200, "mem_available_mb": 6000, "psi_some_avg10": 0.0}

    assert host_health.configured_gate_inert(cur, {"swap_sout": 100}, _cfg(40)) is False


def test_configured_gate_inert_should_be_false_when_gate_would_actually_fire() -> None:
    """combined_low pressure always coincides with mem_available < threshold --
    the gate is reachable, not inert."""
    cur = {"swap_sout": None, "mem_free_mb": 5, "mem_available_mb": 30, "psi_some_avg10": None}

    assert host_health.configured_gate_inert(cur, {"swap_sout": None}, _cfg(40)) is False


def test_memory_pressure_should_use_swap_floor_from_cfg_not_hardcoded_constant() -> None:
    """swap_sout_noise_floor_mb must be read from cfg, not the deleted module
    constant -- a 9 MiB delta clears an 8 MiB floor but not a 32 MiB one.
    PSI is unreadable (None) here so the ladder falls through to tier 2 --
    a readable-and-quiet PSI (e.g. 0.0) would short-circuit at tier 1 and
    never reach the swap-out floor this test exercises."""
    cfg = MonitorHostHealthConfig(
        memory=_HostHealthMemoryConfig(swap_out_noise_floor_mb=8, free_low_mb=16, avail_min_mb=200)
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
        memory=_HostHealthMemoryConfig(swap_out_noise_floor_mb=32, free_low_mb=16, avail_min_mb=200)
    )
    assert host_health.memory_pressure(cur, prev, cfg2) is None  # 9 MiB < 32 MiB floor: no warning


def test_memory_pressure_should_use_psi_full_critical_threshold_from_cfg() -> None:
    """psi_full_avg10_critical must be read from cfg, not the deleted module
    constant (0.2.15's hardcoded 1.0 killed every round on a 1% hiccup)."""
    cfg = MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(full_avg10_critical=60.0))
    healthy = {"psi_some_avg10": 2.0, "psi_full_avg10": 40.0}  # 40 < 60 -- not critical

    assert host_health.memory_pressure(healthy, {}, cfg) is None

    comaing = {"psi_some_avg10": 90.0, "psi_full_avg10": 70.0}  # 70 >= 60 -- critical
    p = host_health.memory_pressure(comaing, {}, cfg)
    assert p is not None and p.severity == "critical" and p.signal == "psi"


def test_memory_pressure_should_be_critical_when_psi_full_avg10_hits_default_critical_bar() -> None:
    """Ladder-calibration rung (D-1 tests-of-record), DEFAULT thresholds:
    psi_full_avg10 >= full_avg10_critical (60.0) is critical on its own --
    the host HAS PSI, the strongest signal, and needs no other corroborating
    reading. Pinned at the exact boundary (60.0, not comfortably above it) to
    lock the `>=` comparison itself, not just "somewhere past the bar"."""
    cfg = MonitorHostHealthConfig()
    sample = {
        "psi_some_avg10": 65.0,
        "psi_full_avg10": 60.0,
        "mem_free_mb": 4000,
        "mem_available_mb": 4000,
    }

    pressure = host_health.memory_pressure(sample, {}, cfg)

    assert pressure is not None
    assert pressure.severity == "critical"
    assert pressure.signal == "psi"


def test_memory_pressure_should_be_critical_when_swap_exceeds_floor_and_memfree_critical() -> None:
    """Ladder-calibration rung (D-1 tests-of-record), DEFAULT thresholds:
    PSI unreadable, swap-out delta ~300MB (comfortably above the default 32
    MiB noise floor) while mem_free_mb (5) is below the default free_low_mb
    (16) -- the "actively dying" critical escalation."""
    cfg = MonitorHostHealthConfig()
    prev = {"swap_sout": 0, "mem_free_mb": 5, "mem_available_mb": 4000, "psi_some_avg10": None}
    cur = {
        "swap_sout": 300 * 1024 * 1024,
        "mem_free_mb": 5,
        "mem_available_mb": 4000,
        "psi_some_avg10": None,
    }

    pressure = host_health.memory_pressure(cur, prev, cfg)

    assert pressure is not None
    assert pressure.severity == "critical"
    assert pressure.signal == "swap_out_rate"


def test_memory_pressure_should_warn_when_swap_exceeds_noise_floor_but_memfree_ample() -> None:
    """Ladder-calibration rung (D-1 tests-of-record), DEFAULT thresholds: the
    same above-floor swap-out delta as the critical rung above, but
    mem_free_mb (200) stays comfortably above free_low_mb (16) -- stays a
    warning, not critical; critical is gated on MemFree, not delta magnitude."""
    cfg = MonitorHostHealthConfig()
    prev = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 4000, "psi_some_avg10": None}
    cur = {
        "swap_sout": 300 * 1024 * 1024,
        "mem_free_mb": 200,
        "mem_available_mb": 4000,
        "psi_some_avg10": None,
    }

    pressure = host_health.memory_pressure(cur, prev, cfg)

    assert pressure is not None
    assert pressure.severity == "warning"
    assert pressure.signal == "swap_out_rate"


def test_memory_pressure_should_warn_combined_low_when_swap_quiet_at_default_thresholds() -> None:
    """Ladder-calibration rung (D-1 tests-of-record), DEFAULT thresholds: PSI
    unreadable, swap-out delta at/below the noise floor (no evidence from
    that tier), mem_free_mb (~5) and mem_available_mb (~82) BOTH below their
    default floors (16 / 200) -- the combined-low warning tier."""
    cfg = MonitorHostHealthConfig()
    prev = {"swap_sout": 0, "mem_free_mb": 5, "mem_available_mb": 82, "psi_some_avg10": None}
    cur = {"swap_sout": 0, "mem_free_mb": 5, "mem_available_mb": 82, "psi_some_avg10": None}

    pressure = host_health.memory_pressure(cur, prev, cfg)

    assert pressure is not None
    assert pressure.severity == "warning"
    assert pressure.signal == "combined_low"


def test_memory_pressure_should_be_healthy_when_memfree_and_memavail_clear_default_thresholds() -> (
    None
):
    """Ladder-calibration rung (D-1 tests-of-record), DEFAULT thresholds: no
    swap movement, mem_free_mb (200) and mem_available_mb (4000) both
    comfortably clear their default floors -- the healthy-verdict rung, the
    baseline every other rung above is calibrated against."""
    cfg = MonitorHostHealthConfig()
    prev = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 4000, "psi_some_avg10": None}
    cur = {"swap_sout": 0, "mem_free_mb": 200, "mem_available_mb": 4000, "psi_some_avg10": None}

    assert host_health.memory_pressure(cur, prev, cfg) is None


def test_memory_pressure_verdict_should_ignore_io_psi_and_full_total_keys() -> None:
    cfg = MonitorHostHealthConfig()
    base = {
        "psi_some_avg10": 1.0,
        "psi_full_avg10": 0.5,
        "mem_available_mb": 400,
        "mem_free_mb": 200,
        "swap_sout": 0,
    }
    poisoned = {
        **base,
        "io_psi_some_avg10": 99.0,
        "io_psi_full_avg10": 99.0,
        "psi_full_total": 10**12,
    }

    a = host_health.memory_pressure(base, {}, cfg)
    b = host_health.memory_pressure(poisoned, {}, cfg)

    assert a == b  # Pressure is a dataclass; verdict+context identical


def test_cgroup_growth_rate_pressure_should_return_none_when_rate_is_none() -> None:

    result = host_health.cgroup_growth_rate_pressure(None, MonitorHostHealthConfig())

    assert result is None


def test_cgroup_growth_rate_pressure_should_return_none_when_rate_below_threshold() -> None:
    cfg = MonitorHostHealthConfig(
        pressure=_HostHealthPressureConfig(cgroup_growth_rate_warning_mb_per_min=512.0)
    )

    result = host_health.cgroup_growth_rate_pressure(400.0, cfg)

    assert result is None


def test_cgroup_growth_rate_pressure_should_warn_when_rate_at_or_above_threshold() -> None:
    cfg = MonitorHostHealthConfig(
        pressure=_HostHealthPressureConfig(cgroup_growth_rate_warning_mb_per_min=512.0)
    )

    result = host_health.cgroup_growth_rate_pressure(512.0, cfg)

    assert result is not None
    assert result.severity == "warning"
    assert result.signal == "cgroup_growth_rate"
    assert result.context == {"rate_mb_per_min": 512.0, "threshold_mb_per_min": 512.0}


def test_cgroup_growth_rate_pressure_should_carry_unrounded_rate_in_context_when_warning() -> None:
    cfg = MonitorHostHealthConfig(
        pressure=_HostHealthPressureConfig(cgroup_growth_rate_warning_mb_per_min=512.0)
    )

    result = host_health.cgroup_growth_rate_pressure(623.456789, cfg)

    assert result is not None
    assert result.context["rate_mb_per_min"] == 623.456789


def test_cgroup_growth_rate_pressure_should_read_threshold_from_cfg_not_hardcoded_constant() -> (
    None
):
    cfg = MonitorHostHealthConfig(
        pressure=_HostHealthPressureConfig(cgroup_growth_rate_warning_mb_per_min=100.0)
    )

    result = host_health.cgroup_growth_rate_pressure(150.0, cfg)

    assert result is not None
