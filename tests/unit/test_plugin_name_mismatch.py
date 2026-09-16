"""A third-party plugin may ship an entry-point name that differs from its
``PluginManifest.name`` (only builtins are pinned equal by
``test_entry_point_names_should_match_manifest_name``). ``peek --json`` must
still list such a plugin under its ENTRY-POINT name in ``pins`` -- keying on
the (unrelated) manifest name would make it vanish from the operator-facing
pin listing entirely.
"""

from __future__ import annotations

from pathlib import Path

import agent_runner
from agent_runner._sandbox_probe import peek_snapshot
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import make_cfg


def _write_mismatch_plugin(tmp_path: Path, module_name: str) -> None:
    """Entry-point name will be 'coolentry', but the module's PLUGIN.name is
    'cool_manifest' (deliberately different)."""
    src = (
        "from agent_runner._plugin_manifest import PluginManifest\n\n\n"
        "PLUGIN = PluginManifest(name='cool_manifest')\n"
    )
    (tmp_path / f"{module_name}.py").write_text(src, encoding="utf-8")


def test_peek_pins_should_list_name_mismatched_plugin_by_entry_name_when_snapshotted(
    tmp_path, monkeypatch
) -> None:
    module_name = "mismatch_peek_pkg"
    _write_mismatch_plugin(tmp_path, module_name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("coolentry", f"{module_name}:PLUGIN")]
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    snap = peek_snapshot(cfg)

    assert "coolentry" in snap["pins"]
