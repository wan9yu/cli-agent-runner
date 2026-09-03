"""Pure interpretation of the memory-pressure signals ``metrics.sample()`` reads.

Cache-poor small-memory hosts inflate kernel ``MemAvailable`` (its reclaimable-
file-cache heuristic assumes cheaply-evictable pages that, on such a host,
don't exist), so a bare ``mem_available_mb < threshold`` gate can be
physically unreachable while the host swaps itself into unresponsiveness.
``metrics.py`` stays the sole OS-talking sampler; this module reads no
filesystem or clock state itself — it only interprets two samples via a
defined signal ladder with graceful degrade:

1. **PSI** (``psi_some_avg10``/``psi_full_avg10``) if readable — the kernel's
   own "reclaim is slowing progress" measure, immune to cache inflation.
2. else **swap-out rate** — the delta of ``swap_sout`` (cumulative) between
   two samples; more than a noise floor of one page means active paging, not
   a benign one-time idle-page swap. A sustained heavy delta (gigabyte-scale
   between samples) escalates to **critical** on its own — a host without PSI
   (``psi=0``, or non-Linux) has no other way to ever reach critical, and an
   actively-paging host is critical whether or not PSI is readable.
3. else **combined-low** — ``mem_free_mb`` AND ``mem_available_mb`` both low
   together. Never a MemFree-only gate: a cache-heavy healthy host's MemFree
   is always low, which would false-positive on every such host.
4. else **no usable signal** — the caller (``detect_mem_pressure``) should
   warn once via ``mem_signal_unavailable`` rather than silently trusting
   ``MemAvailable`` alone (the original bug).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Tier 1 -- PSI avg10 (%-of-time-stalled over the last 10s). `some` = at least
# one task stalled on reclaim; `full` = every task stalled (severe).
_PSI_SOME_AVG10_WARNING = 5.0
_PSI_FULL_AVG10_CRITICAL = 1.0

# Tier 2 -- swap-out delta (bytes) between two samples. A single page (4096B)
# is the kind of benign one-time idle-page swap a fresh boot does; more than
# that between two samples means paging is actively happening now.
_SWAP_SOUT_DELTA_NOISE_FLOOR_BYTES = 4096

# A sustained multi-gigabyte-per-sample swap-out rate is critical on its own,
# PSI or not -- this is the ONLY path to "critical" on a PSI-off host (no
# /proc/pressure/memory, or a kernel built without psi=1), which is exactly
# the field host this ladder exists for: MemAvailable stays inflated right up
# to the coma, so mid-round/pre-round admission gates that only ever escalate
# through PSI would be permanently inert on such a host. 1 GiB between two
# samples is unambiguously heavy, active paging, not noise.
_SWAP_SOUT_DELTA_CRITICAL_BYTES = 1024 * 1024 * 1024

# Tier 3 -- combined-low. MemFree alone is always low on a cache-heavy host
# (the kernel prefers to keep it near-zero and use spare RAM for cache), so
# it must never gate alone -- only together with a low MemAvailable too.
_MEM_FREE_LOW_MB = 16


@dataclass(frozen=True)
class Pressure:
    """A real-pressure verdict from the signal ladder."""

    severity: str  # "warning" | "critical"
    signal: str  # "psi" | "swap_out_rate" | "combined_low"
    message: str
    context: dict[str, Any]


def _swap_sout_delta(sample: dict[str, Any], prev_sample: dict[str, Any]) -> int | None:
    cur = sample.get("swap_sout")
    prev = prev_sample.get("swap_sout")
    if cur is None or prev is None:
        return None
    return cur - prev


def signal_available(sample: dict[str, Any], prev_sample: dict[str, Any]) -> bool:
    """True when at least one ladder tier has a usable reading.

    Distinct from "no pressure detected": a healthy host with readable PSI
    (or a computable swap delta, or MemFree+MemAvailable both present) still
    counts as available even when ``memory_pressure`` returns ``None`` for
    it. Only tier 4 -- nothing usable at all -- reports unavailable.
    """
    if sample.get("psi_some_avg10") is not None:
        return True
    if _swap_sout_delta(sample, prev_sample) is not None:
        return True
    return sample.get("mem_free_mb") is not None and sample.get("mem_available_mb") is not None


def memory_pressure(
    sample: dict[str, Any], prev_sample: dict[str, Any], cfg: Any
) -> Pressure | None:
    """The signal ladder. Returns a verdict on real pressure, or ``None`` when
    the readable tier says healthy OR no tier has a usable reading at all
    (use ``signal_available`` to tell those two apart)."""
    psi_some = sample.get("psi_some_avg10")
    if psi_some is not None:
        psi_full = sample.get("psi_full_avg10") or 0.0
        if psi_full >= _PSI_FULL_AVG10_CRITICAL:
            return Pressure(
                "critical",
                "psi",
                f"PSI memory full avg10={psi_full} (every task stalled on reclaim)",
                {"psi_some_avg10": psi_some, "psi_full_avg10": psi_full},
            )
        if psi_some >= _PSI_SOME_AVG10_WARNING:
            return Pressure(
                "warning",
                "psi",
                f"PSI memory some avg10={psi_some} (reclaim is slowing progress)",
                {"psi_some_avg10": psi_some, "psi_full_avg10": psi_full},
            )
        return None  # PSI is readable and says healthy -- trust it, no fall-through.

    delta = _swap_sout_delta(sample, prev_sample)
    if delta is not None and delta > _SWAP_SOUT_DELTA_NOISE_FLOOR_BYTES:
        if delta >= _SWAP_SOUT_DELTA_CRITICAL_BYTES:
            return Pressure(
                "critical",
                "swap_out_rate",
                f"swap sout +{delta}B since last sample (sustained heavy paging)",
                {"swap_sout_delta": delta},
            )
        return Pressure(
            "warning",
            "swap_out_rate",
            f"swap sout +{delta}B since last sample (active paging)",
            {"swap_sout_delta": delta},
        )
    # Swap tier gave no evidence either way (no data, or below the noise
    # floor -- e.g. a swapless/zram-less host where sout never moves) --
    # that is NOT the same as "healthy": fall through to combined-low rather
    # than stopping here, or real pressure on such a host would go unseen.

    mem_free = sample.get("mem_free_mb")
    mem_avail = sample.get("mem_available_mb")
    if mem_free is not None and mem_avail is not None:
        if mem_free < _MEM_FREE_LOW_MB and mem_avail < cfg.mem_avail_min_mb:
            return Pressure(
                "warning",
                "combined_low",
                f"mem_free_mb {mem_free} < {_MEM_FREE_LOW_MB} and "
                f"mem_available_mb {mem_avail} < {cfg.mem_avail_min_mb}",
                {"mem_free_mb": mem_free, "mem_available_mb": mem_avail},
            )
        return None

    return None  # tier 4 -- no usable signal; see signal_available()


def configured_gate_inert(sample: dict[str, Any], prev_sample: dict[str, Any], cfg: Any) -> bool:
    """True when the configured ``mem_available_mb < mem_avail_min_mb`` gate is
    provably inert on this host: a cache-poor-valid signal (PSI or swap-out
    rate -- NOT combined-low, which can only fire when MemAvailable is
    already below threshold, so it can never coexist with this condition)
    reports real pressure while ``mem_available_mb`` stays at/above the
    configured threshold.

    Deliberately NOT "MemAvailable >> MemFree" -- that divergence is true on
    every healthy warm-cache host and would train operators to ignore the
    channel.
    """
    mem_avail = sample.get("mem_available_mb")
    if mem_avail is None or mem_avail < cfg.mem_avail_min_mb:
        return False  # the gate would fire (or we can't evaluate it) -- not inert
    pressure = memory_pressure(sample, prev_sample, cfg)
    return pressure is not None and pressure.signal in ("psi", "swap_out_rate")
