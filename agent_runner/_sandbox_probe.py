"""Capability probe for the Tier-B (Landlock + seccomp) sandbox trampoline.

Pure and side-effect-free: it never raises, and it never engages any
confinement on the CALLING process. In particular ``_probe_seccomp`` only
CONSTRUCTS a ``SyscallFilter`` object to prove the binding + shared library
are usable -- it never calls ``.load()``, which installs a filter on the
current process and cannot be undone. Loading/applying a real filter is the
trampoline child's job (``_plugin_sandbox.py``), never this probe's.

Reports the best tier this host can actually achieve, so every caller --
the fail-closed load gate (``agent_runner.__init__._admit_third_party``),
``doctor``, the serve-boot gate (``gate_serve_boot``), and the spawn-seam
degrade path -- reports the SAME number instead of each re-deriving
platform logic.

The ``[sandbox]`` extra (``py-landlock``/``pyseccomp``) is Linux-only and
imported LAZILY inside the two ``_probe_*`` helpers below -- never at module
scope -- so this module imports cleanly on a base (non-``[sandbox]``)
install and on non-Linux hosts (this dev host included).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AchievedTier = Literal["landlock+seccomp", "landlock", "seccomp", "unconfined"]

TIER_B_PROTOCOLS = ("spawn_hooks", "dirty_handlers")
"""Extension surfaces routed through the Tier-B process-isolation trampoline.
Tier-A hooks (detectors, context enrichers, pre/post-round, serve-startup)
run in-process and are never candidates for these confinement tiers."""


@dataclass(frozen=True)
class SandboxProbe:
    """What this host can actually ACHIEVE, not what config asked for --
    ``achieved_tier`` may fall short of ``[plugins] sandbox = "require"``,
    which is exactly what the load gate and ``doctor`` need to know."""

    achieved_tier: AchievedTier
    landlock_abi: int | None
    seccomp: bool
    unconfined_reason: str | None


def _probe_landlock() -> tuple[int | None, str | None]:
    """Returns (abi, reason); never raises."""
    try:
        from py_landlock import LandlockError, get_abi_version
    except ImportError:
        return None, "[sandbox] extra not installed"
    except Exception as e:  # noqa: BLE001 — binding import misbehaved; report, don't crash
        return None, f"landlock binding failed to import: {e}"
    try:
        return get_abi_version(), None
    except LandlockError as e:
        return None, f"landlock unavailable: {e}"
    except Exception as e:  # noqa: BLE001 — fail-closed to "no landlock", never propagate
        return None, f"landlock unavailable: {e}"


def _probe_seccomp() -> tuple[bool, str | None]:
    """Returns (ok, reason); never raises. Only CONSTRUCTS a ``SyscallFilter``
    (never ``.load()``s it) to confirm ``libseccomp.so.2`` is loadable without
    installing anything on this process."""
    try:
        import pyseccomp as seccomp
    except ImportError:
        return False, "[sandbox] extra not installed"
    except OSError as e:
        return False, f"libseccomp unavailable: {e}"
    try:
        seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    except OSError as e:
        return False, f"libseccomp unavailable: {e}"
    except Exception as e:  # noqa: BLE001 — fail-closed to "no seccomp", never propagate
        return False, f"seccomp unavailable: {e}"
    return True, None


def probe_sandbox_capability() -> SandboxProbe:
    """Best-effort probe of the confinement this host can achieve right now.

    Never raises: any binding/library failure narrows the achieved tier
    toward ``"unconfined"`` with a reason, since every caller uses this for
    a fail-closed trust decision -- a probe that could itself raise would
    take the load gate and ``doctor`` down with it.
    """
    if sys.platform != "linux":
        return SandboxProbe(
            achieved_tier="unconfined",
            landlock_abi=None,
            seccomp=False,
            unconfined_reason="non-linux",
        )

    landlock_abi, landlock_reason = _probe_landlock()
    seccomp_ok, seccomp_reason = _probe_seccomp()

    if landlock_abi is not None and seccomp_ok:
        return SandboxProbe("landlock+seccomp", landlock_abi, True, None)
    if landlock_abi is not None:
        return SandboxProbe("landlock", landlock_abi, False, None)
    if seccomp_ok:
        return SandboxProbe("seccomp", None, True, None)
    return SandboxProbe(
        achieved_tier="unconfined",
        landlock_abi=None,
        seccomp=False,
        unconfined_reason=seccomp_reason or landlock_reason or "sandbox capability unavailable",
    )


def gate_serve_boot(cfg, log_dir: Path) -> bool:
    """Called ONCE at serve boot, before the round loop starts. Returns False
    to abort serve (the caller releases the serve lock and exits with
    ``_serve_policy.PERMANENT_CONFIG_EXIT`` -- a permanent config error, not a
    transient one, so systemd/day-2 doesn't restart-loop on it).

    Gates confinement for the Tier-B (Landlock + seccomp) trampoline that
    isolates ``spawn_hooks``/``dirty_handlers`` (see ``TIER_B_PROTOCOLS``)
    ONLY. Tier-A hooks (detectors, context enrichers, pre/post-round,
    serve-startup) run in-process today regardless of this gate's outcome --
    whole-supervisor Landlock confinement is deferred, not delivered by this
    gate. ``achieved_tier == "landlock+seccomp"`` therefore means "the Tier-B
    trampoline can fully confine," never "the whole supervisor is sandboxed."

    Tri-state ``[plugins] sandbox``:
    - ``"off"``: no probe, no event -- always proceeds.
    - ``"require"``: the Tier-B trampoline MUST be able to fully confine.
      When it can't, emits ``plugin_sandbox_degraded`` and returns False --
      loud, deterministic, fail-closed (never silently serves unconfined).
    - ``"prefer"``: proceeds either way; when confinement can't fully
      engage, emits ``plugin_sandbox_degraded`` exactly once (this call is
      the only emit site for the mechanism itself -- once per serve boot,
      never per round) and continues serving unconfined.

    A single call site, run once before the round loop, is what makes "once
    per serve boot" true by construction -- there is no per-round re-probe.
    """
    from agent_runner.api import emit_plugin_sandbox_degraded

    if cfg.plugins.sandbox == "off":
        return True
    probe = probe_sandbox_capability()
    if probe.achieved_tier == "landlock+seccomp":
        return True
    reason = probe.unconfined_reason or f"achieved {probe.achieved_tier}"
    emit_plugin_sandbox_degraded(
        log_dir, requested=cfg.plugins.sandbox, achieved_tier=probe.achieved_tier, reason=reason
    )
    return cfg.plugins.sandbox != "require"
