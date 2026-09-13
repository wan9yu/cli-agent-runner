"""sha256 pinning of a plugin's declared entry-point module FILE (coarse but
deterministic — one file, one read). Fail-closed: an unresolvable/unreadable
module raises, and the loader turns that into a refusal, same as a mismatch.

Keyed on the entry-point NAME (the ``agent_runner.plugins`` group's own
pyproject.toml key), not ``PluginManifest.name`` — a third-party plugin
hasn't been imported yet at pin-check time, so its manifest name isn't even
known; the entry-point name is the only stable, pre-import identifier an
operator can write into ``[plugins.pin]``. See
``agent_runner.__init__._admit_third_party`` for the call site.
"""

from __future__ import annotations

import hashlib
import importlib.util
from typing import Literal

PinVerdict = Literal["verified", "mismatch", "unpinned"]


def compute_plugin_checksum(module_path: str) -> str:
    """sha256 of the resolved module's source FILE, as ``"sha256:<64hex>"``.

    This exact recipe (sha256 of the raw bytes of ``find_spec(module_path)
    .origin``) is also what ``doctor`` prints and what ``verify_pin`` checks
    against — an operator copy-pastes doctor's printed digest straight into
    ``[plugins.pin]``, so all three MUST compute the identical value.
    """
    spec = importlib.util.find_spec(module_path)
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError(f"cannot resolve module file for {module_path!r}")
    with open(spec.origin, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return f"sha256:{digest}"


def verify_pin(name: str, module_path: str, pins: dict[str, str]) -> tuple[PinVerdict, str | None]:
    """Returns ``(verdict, actual)`` where ``actual`` is the computed
    ``"sha256:<hex>"`` digest of the module file, or ``None`` when the name is
    unpinned (no digest is computed in that case). The caller reuses ``actual``
    for the mismatch event instead of hashing the file a second time.

    ``name`` is the entry-point name (the ``[plugins.pin]`` key an operator
    writes), NOT ``PluginManifest.name`` — see module docstring."""
    if name not in pins:
        return "unpinned", None
    actual = compute_plugin_checksum(module_path)
    return ("verified" if actual == pins[name] else "mismatch"), actual
