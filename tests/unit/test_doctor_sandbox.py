"""doctor_snapshot -- the SINGLE source for doctor's sandbox report (consolidates
what were previously two doctor_cmd-local helpers, _sandbox_report and
_third_party_plugin_checksums, into one _sandbox_probe function). The
security-relevant case is the same one peek_snapshot guards: third-party
classification must key on is_builtin_provenance(name, module_path), never a
bare `name in BUILTIN_PLUGIN_NAMES` -- a name-squatter (reserved name, foreign
module) must still surface in third_party_plugin_hashes, or an operator loses
exactly the visibility this report exists to provide."""

from __future__ import annotations

from pathlib import Path

from agent_runner._sandbox_probe import doctor_snapshot
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import make_cfg


def test_doctor_snapshot_should_report_libseccomp_presence_as_bool(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    snap = doctor_snapshot(cfg)

    assert isinstance(snap["libseccomp_present"], bool)
    assert isinstance(snap["third_party_plugin_hashes"], dict)


def test_doctor_snapshot_should_omit_builtins_from_hashes(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    snap = doctor_snapshot(cfg)

    assert "default_dirty_handler" not in snap["third_party_plugin_hashes"]


def test_doctor_snapshot_should_surface_a_name_squatter_in_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    """A third-party entry claiming the reserved builtin name "pi" but
    resolving to a FOREIGN module is a name-squatter. It must appear in
    third_party_plugin_hashes, never silently hidden as a trusted builtin --
    proving doctor_snapshot keys on is_builtin_provenance(name, module_path),
    not on a bare `name in BUILTIN_PLUGIN_NAMES`."""
    import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("pi", "agent_runner._plugin_checksum:compute_plugin_checksum")],
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    snap = doctor_snapshot(cfg)

    assert "pi" in snap["third_party_plugin_hashes"]


def test_doctor_snapshot_should_omit_a_genuine_builtin_named_pi_from_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    """Contrast case: the SAME reserved name "pi" resolving to its genuine
    agent_runner.builtin_plugins module is excluded -- builtins never get a
    pin-me hash; only third-party plugins appear at all."""
    import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("pi", "agent_runner.builtin_plugins.pi:PLUGIN")],
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    snap = doctor_snapshot(cfg)

    assert "pi" not in snap["third_party_plugin_hashes"]
