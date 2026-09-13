"""peek --json's plugins.sandbox / plugins.pins / plugins.spawn_override_allow
block (_sandbox_probe.peek_snapshot). The security-relevant case is the
provenance one: pins categorization must key on is_builtin_provenance(name,
module_path), never on a bare name match -- a third-party plugin that
name-squats a reserved builtin name must still surface as third-party, or an
operator loses exactly the visibility this block exists to provide."""

from __future__ import annotations

from pathlib import Path

from agent_runner._sandbox_probe import TIER_B_PROTOCOLS, peek_snapshot
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import make_cfg


def test_peek_snapshot_should_report_honest_covers_list_when_called(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="prefer"))

    snap = peek_snapshot(cfg)

    assert snap["sandbox"]["covers"] == list(TIER_B_PROTOCOLS)
    assert snap["sandbox"]["requested"] == "prefer"


def test_peek_snapshot_should_echo_spawn_override_allow_when_configured(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_a"]))

    snap = peek_snapshot(cfg)

    assert snap["spawn_override_allow"] == ["gate_a"]


def test_peek_snapshot_should_include_achieved_capability_fields_when_called(
    tmp_path: Path,
) -> None:
    cfg = make_cfg(tmp_path)

    snap = peek_snapshot(cfg)

    sandbox = snap["sandbox"]
    assert "achieved_tier" in sandbox
    assert "landlock_abi" in sandbox
    assert "seccomp" in sandbox
    assert "unconfined_reason" in sandbox


def test_peek_snapshot_should_surface_a_name_squatter_as_third_party_when_pins_checked(
    tmp_path: Path, monkeypatch
) -> None:
    """A third-party entry claiming the reserved builtin name "pi" but
    resolving to a FOREIGN module is a name-squatter (see
    agent_runner._warn_builtin_name_squat). It must appear in plugins.pins as
    third-party (unpinned/mismatch), never silently hidden as a trusted
    builtin -- proving peek keys on is_builtin_provenance(name, module_path),
    not on a bare `name in BUILTIN_PLUGIN_NAMES`."""
    import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("pi", "evil_pkg.pi:PLUGIN")],
    )
    monkeypatch.setattr("agent_runner._plugin_manifest.loaded_manifest_names", lambda: ["pi"])
    cfg = make_cfg(tmp_path)

    snap = peek_snapshot(cfg)

    assert "pi" in snap["pins"]
    assert snap["pins"]["pi"] in ("unpinned", "mismatch")


def test_peek_snapshot_should_omit_a_genuine_builtin_from_pins_when_called(
    tmp_path: Path, monkeypatch
) -> None:
    """Contrast case: the SAME reserved name "pi" resolving to its genuine
    agent_runner.builtin_plugins module is excluded from pins entirely --
    builtins are never "unpinned"; only third-party plugins appear at all."""
    import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("pi", "agent_runner.builtin_plugins.pi:PLUGIN")],
    )
    monkeypatch.setattr("agent_runner._plugin_manifest.loaded_manifest_names", lambda: ["pi"])
    cfg = make_cfg(tmp_path)

    snap = peek_snapshot(cfg)

    assert "pi" not in snap["pins"]


def test_peek_snapshot_should_report_verified_when_pin_matches_module_checksum(
    tmp_path: Path, monkeypatch
) -> None:
    from agent_runner._plugin_checksum import compute_plugin_checksum

    module_path = "agent_runner._plugin_checksum"
    digest = compute_plugin_checksum(module_path)
    import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("acme", f"{module_path}:compute_plugin_checksum")],
    )
    monkeypatch.setattr("agent_runner._plugin_manifest.loaded_manifest_names", lambda: ["acme"])
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(pin={"acme": digest}))

    snap = peek_snapshot(cfg)

    assert snap["pins"]["acme"] == "verified"


def test_peek_snapshot_should_report_mismatch_when_pin_does_not_match(
    tmp_path: Path, monkeypatch
) -> None:
    import agent_runner

    module_path = "agent_runner._plugin_checksum"
    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("acme", f"{module_path}:compute_plugin_checksum")],
    )
    monkeypatch.setattr("agent_runner._plugin_manifest.loaded_manifest_names", lambda: ["acme"])
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(pin={"acme": "sha256:" + "0" * 64}))

    snap = peek_snapshot(cfg)

    assert snap["pins"]["acme"] == "mismatch"
