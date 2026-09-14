"""A third-party plugin may ship an entry-point name that differs from its
``PluginManifest.name`` (only builtins are pinned equal by
``test_entry_point_names_should_match_manifest_name``). The trampoline must
resolve such a plugin by the DISCOVERED module path threaded per-handler at
registration -- never by the collidable manifest name -- or
``run_hook_sandboxed`` raises ``LookupError``, the dirty handler is silently
inert, and the plugin vanishes from peek's ``pins``.
"""

from __future__ import annotations

from pathlib import Path

import agent_runner
from agent_runner import hooks
from agent_runner._sandbox_probe import peek_snapshot
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import make_cfg, make_hook_context


def _write_mismatch_plugin(tmp_path: Path, module_name: str) -> None:
    """Entry-point name will be 'coolentry', but the module's PLUGIN.name is
    'cool_manifest' (deliberately different) with a dirty handler 'cool_dirty'."""
    src = (
        "from agent_runner._plugin_manifest import PluginManifest\n"
        "from agent_runner.api_types import DirtyOutcome\n\n\n"
        "class _H:\n"
        "    name = 'cool_dirty'\n"
        "    priority = 0\n\n"
        "    def handle_dirty(self, ctx, dirty_files):\n"
        "        return DirtyOutcome(kind='committed', ref='ok')\n\n\n"
        "PLUGIN = PluginManifest(name='cool_manifest', dirty_handlers=(_H(),))\n"
    )
    (tmp_path / f"{module_name}.py").write_text(src, encoding="utf-8")


def test_dispatch_should_run_name_mismatched_dirty_handler_when_entry_name_differs(
    tmp_path, monkeypatch
) -> None:
    module_name = "mismatch_dispatch_pkg"
    _write_mismatch_plugin(tmp_path, module_name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("coolentry", f"{module_name}:PLUGIN")]
    )
    agent_runner.load_and_register_plugins(PluginsConfig())
    handler = next(h for h in hooks._DIRTY_HANDLERS if h.name == "cool_dirty")

    outcome = hooks.dispatch_dirty(
        make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer"
    )

    assert hooks._DIRTY_HANDLER_MODULE[id(handler)] == (module_name, "PLUGIN")
    assert outcome is not None and outcome.kind == "committed"


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
