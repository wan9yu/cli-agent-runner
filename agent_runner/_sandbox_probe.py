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


_SANDBOX_EXTRA_HINT = (
    "install the sandbox extra to enable confinement: pip install cli-agent-runner[sandbox]"
)


def sandbox_engaged() -> bool:
    """True iff the Tier-B trampoline can FULLY confine on this host right now
    (``achieved_tier == "landlock+seccomp"``) -- the SINGLE predicate the
    dispatch seams route on (see :func:`hook_route`). Probe once at boot and
    thread the result to the seams; never re-probe per round."""
    return probe_sandbox_capability().achieved_tier == "landlock+seccomp"


def hook_route(
    sandbox: str, *, third_party: bool, engaged: bool
) -> Literal["in_process", "trampoline", "refuse"]:
    """Where to run one hook, given the operator's ``[plugins] sandbox`` mode and
    the BOOT PROBE VERDICT ``engaged`` -- never a platform-only heuristic.

    ``sandbox == "off"`` or a genuine builtin (``not third_party``) always runs
    in-process. A third-party hook under confinement trampolines only when the
    sandbox actually ENGAGES; when it can't, ``prefer`` runs it in-process
    (unconfined -- announced ONCE at boot via ``plugin_sandbox_degraded``, never
    per hook) and ``require`` refuses it (belt-and-braces: ``gate_serve_boot``
    already aborts serve under require+can't-engage, so this is the round child's
    own last line). Symmetric across ``dispatch_dirty`` and the spawn seam."""
    if sandbox == "off" or not third_party:
        return "in_process"
    if engaged:
        return "trampoline"
    return "refuse" if sandbox == "require" else "in_process"


def gate_serve_boot(cfg, log_dir: Path) -> tuple[bool, bool]:
    """Called ONCE at serve boot, before the round loop starts. Returns
    ``(proceed, engaged)``: ``proceed`` False aborts serve (the caller releases
    the serve lock and exits with ``_serve_policy.PERMANENT_CONFIG_EXIT`` -- a
    permanent config error, not a transient one, so systemd/day-2 doesn't
    restart-loop on it); ``engaged`` is the boot probe verdict
    (``achieved_tier == "landlock+seccomp"``) the caller THREADS to the dispatch
    seams so they route without re-probing per round (see :func:`hook_route`).

    Gates confinement for the Tier-B (Landlock + seccomp) trampoline that
    isolates ``spawn_hooks``/``dirty_handlers`` (see ``TIER_B_PROTOCOLS``)
    ONLY. Tier-A hooks (detectors, context enrichers, pre/post-round,
    serve-startup) run in-process today regardless of this gate's outcome --
    whole-supervisor Landlock confinement is deferred, not delivered by this
    gate. ``achieved_tier == "landlock+seccomp"`` therefore means "the Tier-B
    trampoline can fully confine," never "the whole supervisor is sandboxed."

    Tri-state ``[plugins] sandbox``:
    - ``"off"``: no probe, no event -- always proceeds (``engaged`` False, but
      the seams route ``off`` to in-process regardless).
    - ``"require"``: the Tier-B trampoline MUST be able to fully confine.
      When it can't, emits ``plugin_sandbox_degraded`` and returns
      ``(False, False)`` -- loud, deterministic, fail-closed (never silently
      serves unconfined).
    - ``"prefer"``: proceeds either way; when confinement can't fully
      engage, emits ``plugin_sandbox_degraded`` exactly once (this call is
      the only emit site for the mechanism itself -- once per serve boot,
      never per round) and continues serving unconfined.

    A single call site, run once before the round loop, is what makes "once
    per serve boot" true by construction -- there is no per-round re-probe.
    """
    from agent_runner.api import emit_plugin_sandbox_degraded

    if cfg.plugins.sandbox == "off":
        return True, False
    probe = probe_sandbox_capability()
    if probe.achieved_tier == "landlock+seccomp":
        return True, True
    base = probe.unconfined_reason or f"achieved {probe.achieved_tier}"
    emit_plugin_sandbox_degraded(
        log_dir,
        requested=cfg.plugins.sandbox,
        achieved_tier=probe.achieved_tier,
        reason=f"{base}; {_SANDBOX_EXTRA_HINT}",
    )
    return cfg.plugins.sandbox != "require", False


def _iter_third_party_entries():
    """Yield ``(name, module_path)`` for every discovered entry point that is
    NOT a genuine builtin (``is_builtin_provenance``) -- the identical
    discover-and-classify loop ``peek_snapshot`` and ``doctor_snapshot`` each
    ran inline. Classification is by PROVENANCE, never a bare
    ``name in BUILTIN_PLUGIN_NAMES`` -- see either snapshot's docstring for
    why a name-squatter must still surface as third-party."""
    import agent_runner
    from agent_runner._registry import is_builtin_provenance

    for name, value in agent_runner._DISCOVERED_PLUGIN_ENTRIES:
        module_path = agent_runner._entry_point_module_path(value)
        if is_builtin_provenance(name, module_path):
            continue
        yield name, module_path


def peek_snapshot(cfg) -> dict:
    """Assemble ``peek --json``'s ``plugins.sandbox`` / ``plugins.pins`` /
    ``plugins.spawn_override_allow`` block from one probe + ``cfg``.
    Read-only: never engages confinement, never (re-)imports a plugin module.

    ``sandbox.covers`` is the honesty mechanism: it names ONLY the Tier-B
    families the trampoline actually confines (``TIER_B_PROTOCOLS`` --
    ``spawn_hooks``/``dirty_handlers``). Tier-A hooks (detectors, context
    enrichers, pre/post-round, serve-startup) run in-process and are never
    confined by this mechanism, so this list must never imply otherwise.

    ``pins`` categorizes third-party-vs-builtin by
    ``is_builtin_provenance(name, module_path)`` -- NEVER by a bare
    ``name in BUILTIN_PLUGIN_NAMES``. A plugin that name-squats a reserved
    builtin name (foreign module, reserved name -- see
    ``agent_runner._warn_builtin_name_squat``) must still surface here as
    third-party (verified/mismatch/unpinned); keying on the name alone would
    hide a squatter as a trusted builtin, masking exactly the threat this
    block exists to make visible.

    Keys ``pins`` by ENTRY-POINT name (iterating ``_DISCOVERED_PLUGIN_ENTRIES``
    directly), NOT by ``manifest.name`` -- an entry-point name that differs from
    the manifest name (legal for third-party plugins) would otherwise
    ``entries.get(manifest_name) -> None`` and the plugin would vanish from
    ``pins`` entirely. Mirrors ``doctor_snapshot``'s identical recipe.
    """
    from agent_runner._plugin_checksum import verify_pin

    probe = probe_sandbox_capability()
    pins: dict[str, str] = {}
    for name, module_path in _iter_third_party_entries():
        try:
            verdict, _actual = verify_pin(name, module_path, cfg.plugins.pin)
        except Exception:  # noqa: BLE001 — display only, never fail peek on a broken plugin
            verdict = "mismatch"
        pins[name] = verdict
    return {
        "sandbox": {
            "requested": cfg.plugins.sandbox,
            "achieved_tier": probe.achieved_tier,
            "landlock_abi": probe.landlock_abi,
            "seccomp": probe.seccomp,
            "unconfined_reason": probe.unconfined_reason,
            "covers": list(TIER_B_PROTOCOLS),
        },
        "pins": pins,
        "spawn_override_allow": list(cfg.plugins.spawn_override_allow),
    }


def doctor_snapshot(cfg) -> dict:
    """``doctor``'s read-only sandbox report -- the SINGLE source for
    ``doctor``'s sandbox block (folds what were two doctor_cmd-local helpers,
    ``_sandbox_report`` and ``_third_party_plugin_checksums``, into one).

    ``libseccomp_present`` is a cheap presence probe
    (``ctypes.util.find_library``) distinct from ``probe_sandbox_capability``'s
    filter-CONSTRUCTING check -- it answers "is ``libseccomp.so.2`` even
    installed on this host" rather than "can this process actually build a
    filter with it," since ``pyseccomp`` is a ctypes shim over that shared
    library.

    ``third_party_plugin_hashes`` gives every discovered THIRD-PARTY
    (non-builtin) plugin's computed sha256, keyed by entry-point name -- the
    same name and the same recipe ``verify_pin`` checks against, so an
    operator can copy-paste a printed value straight into ``[plugins.pin]``.
    Iterates every DISCOVERED entry (not only successfully-loaded manifests)
    so a plugin refused by the load gate still gets a hash an operator can
    act on.

    Classification uses ``is_builtin_provenance(name, module_path)``, NEVER a
    bare ``name in BUILTIN_PLUGIN_NAMES`` -- a name-squatter (reserved name,
    foreign module) must still surface here so an operator can catch it;
    keying on the name alone would hide a squatter as a trusted builtin,
    masking exactly the threat this report exists to make visible. Mirrors
    ``peek_snapshot``'s identical recipe.
    """
    import ctypes.util

    from agent_runner._plugin_checksum import compute_plugin_checksum

    probe = probe_sandbox_capability()
    hashes: dict[str, str] = {}
    for name, module_path in _iter_third_party_entries():
        try:
            hashes[name] = compute_plugin_checksum(module_path)
        except Exception as e:  # noqa: BLE001 — doctor reports, never crashes, on a broken plugin
            hashes[name] = f"<unresolvable: {e}>"
    return {
        "requested": cfg.plugins.sandbox,
        "achieved_tier": probe.achieved_tier,
        "landlock_abi": probe.landlock_abi,
        "seccomp": probe.seccomp,
        "libseccomp_present": ctypes.util.find_library("seccomp") is not None,
        "unconfined_reason": probe.unconfined_reason,
        "third_party_plugin_hashes": hashes,
    }
