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
2. else **swap-out rate** — the delta of ``swap_sout`` (cumulative bytes) over
   the ``prev_sample`` the caller passes; more than the configured noise floor
   (tens of MB by default) is flagged as pressure — above the noise floor, not
   benign startup/idle churn. Escalates to
   **critical** when ``mem_free_mb`` is ALSO critically low (the same absolute
   floor tier 3 uses below) WHILE swap-out is positive — the unambiguous
   "actively dying" state. This is gated on MemFree, not on the swap-out
   RATE's magnitude: a small, memory-starved host on a slow SD-card/USB-backed
   swap device may push only a few hundred MB across an entire dying episode,
   far below any defensible flat per-sample byte-rate, so a rate-only critical
   bar was inert on exactly the host this ladder exists for. What ``prev``
   spans is the caller's choice: the pre-round gate diffs successive round
   boundaries, and ``serve``'s mid-round floor passes each tick's PREVIOUS
   sample as ``prev`` (reassigned every ~10s tick, not pinned to round-start),
   so its delta is a PER-INTERVAL rate that can fall back below the noise
   floor on the very next tick rather than accumulating for the whole round.
   Sustained pressure is that caller's job (a consecutive-critical-samples
   counter), not this ladder's — a single above-floor sample is one data
   point, not a verdict on host health over time. A host without PSI
   (``psi=0``, or non-Linux) has no OTHER way to reach critical.
3. else **combined-low** — ``mem_free_mb`` AND ``mem_available_mb`` both low
   together. Never a MemFree-only gate: a cache-heavy healthy host's MemFree
   is always low, which would false-positive on every such host. (Tier 2's
   critical escalation above is not a MemFree-only gate either — it also
   requires an actual measured, above-floor swap-out delta.)
4. else **no usable signal** — the caller (``detect_mem_pressure``) should
   warn once via ``mem_signal_unavailable`` rather than silently trusting
   ``MemAvailable`` alone (the original bug).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Tier 1 -- PSI avg10 (%-of-time-stalled over the last 10s). `some` = at least
# one task stalled on reclaim; `full` = every task stalled (severe).
# Config-tunable (``MonitorHostHealthConfig.psi_full_avg10_critical`` /
# ``.psi_some_avg10_warning``) -- see that dataclass for the defaults and the
# rationale for the 60% critical bar (systemd-oomd's proven
# DefaultMemoryPressureLimit; a sustained near-total stall, not a 1% hiccup).

# Tier 2 -- swap-out delta (bytes) between two samples. Below
# cfg.swap_sout_noise_floor_mb is noise: tens of MB is ordinary startup/idle
# churn between successive samples (round boundaries, or the mid-round loop's
# own ~10s ticks) and is NOT, on its own, a real paging signal -- a single
# page (4096B, the old floor) is far too sensitive and made the pre-round gate
# defer/resume on a few idle KB of swap movement every round. Config-tunable
# (``MonitorHostHealthConfig.swap_sout_noise_floor_mb``, default 32 MiB) so a
# tiny host can lower it and a large host can raise it.

# Tier 3 -- combined-low. MemFree alone is always low on a cache-heavy host
# (the kernel prefers to keep it near-zero and use spare RAM for cache), so
# it must never gate alone -- only together with a low MemAvailable (tier 3)
# or an actual measured, above-floor swap-out delta (tier 2's critical
# escalation, below). Also doubles as the critical-escalation MemFree floor:
# "critically low" is the same absolute bar either way. Config-tunable
# (``MonitorHostHealthConfig.mem_free_low_mb``, default 16 MiB).


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
        if psi_full >= cfg.psi_full_avg10_critical:
            return Pressure(
                "critical",
                "psi",
                f"PSI memory full avg10={psi_full} (every task stalled on reclaim)",
                {"psi_some_avg10": psi_some, "psi_full_avg10": psi_full},
            )
        if psi_some >= cfg.psi_some_avg10_warning:
            return Pressure(
                "warning",
                "psi",
                f"PSI memory some avg10={psi_some} (reclaim is slowing progress)",
                {"psi_some_avg10": psi_some, "psi_full_avg10": psi_full},
            )
        return None  # PSI is readable and says healthy -- trust it, no fall-through.

    delta = _swap_sout_delta(sample, prev_sample)
    if delta is not None and delta > cfg.swap_sout_noise_floor_mb * 1024 * 1024:
        mem_free = sample.get("mem_free_mb")
        if mem_free is not None and mem_free < cfg.mem_free_low_mb:
            return Pressure(
                "critical",
                "swap_out_rate",
                f"swap sout +{delta}B since last sample while mem_free_mb {mem_free} "
                f"< {cfg.mem_free_low_mb} (actively dying, independent of swap-device speed)",
                {"swap_sout_delta": delta, "mem_free_mb": mem_free},
            )
        return Pressure(
            "warning",
            "swap_out_rate",
            f"swap sout +{delta}B since last sample (above the noise floor)",
            {"swap_sout_delta": delta},
        )
    # Swap tier gave no evidence either way (no data, or below the noise
    # floor -- e.g. a swapless/zram-less host where sout never moves) --
    # that is NOT the same as "healthy": fall through to combined-low rather
    # than stopping here, or real pressure on such a host would go unseen.

    mem_free = sample.get("mem_free_mb")
    mem_avail = sample.get("mem_available_mb")
    if mem_free is not None and mem_avail is not None:
        if mem_free < cfg.mem_free_low_mb and mem_avail < cfg.mem_avail_min_mb:
            return Pressure(
                "warning",
                "combined_low",
                f"mem_free_mb {mem_free} < {cfg.mem_free_low_mb} and "
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
