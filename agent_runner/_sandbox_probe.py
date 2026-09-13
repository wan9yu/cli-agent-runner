"""Capability probe for the Tier-B (Landlock + seccomp) sandbox trampoline.

Pure and side-effect-free: it never raises, and it never engages any
confinement on the CALLING process. In particular ``_probe_seccomp`` only
CONSTRUCTS a ``SyscallFilter`` object to prove the binding + shared library
are usable -- it never calls ``.load()``, which installs a filter on the
current process and cannot be undone. Loading/applying a real filter is the
trampoline child's job (``_plugin_sandbox.py``), never this probe's.

Reports the best tier this host can actually achieve, so every caller --
the fail-closed load gate (``agent_runner.__init__._admit_third_party``),
``doctor``, and the spawn-seam degrade path -- reports the SAME number
instead of each re-deriving platform logic.

The ``[sandbox]`` extra (``py-landlock``/``pyseccomp``) is Linux-only and
imported LAZILY inside the two ``_probe_*`` helpers below -- never at module
scope -- so this module imports cleanly on a base (non-``[sandbox]``)
install and on non-Linux hosts (this dev host included).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
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
