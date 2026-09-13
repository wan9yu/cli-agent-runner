from __future__ import annotations

import pytest

import agent_runner
from agent_runner import _plugin_manifest, events, hooks
from agent_runner._plugin_manifest import PluginManifest, loaded_manifest_names
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import isolating
from tests._test_helpers import read_events_for_current_month as read_events

_reset = isolating(
    hooks._POST_ROUND_HOOKS,
    hooks._DIRTY_HANDLERS,
    hooks._DIRTY_HANDLER_OWNER,
    hooks._DIRTY_HANDLER_BUILTIN,
    _plugin_manifest._LOADED_MANIFESTS,
)


class _Marker:
    name = "fc_hook"

    def after_round(self, ctx, result):
        pass


PLUGIN = PluginManifest(name="fc_thirdparty", post_round_hooks=(_Marker(),))


def test_emit_checksum_mismatch_should_carry_expected_and_actual(tmp_path):
    from agent_runner.api import emit_plugin_checksum_mismatch

    emit_plugin_checksum_mismatch(tmp_path, name="acme", expected="sha256:a", actual="sha256:b")

    rec = [e for e in read_events(tmp_path) if e["event"] == events.PLUGIN_CHECKSUM_MISMATCH]
    assert rec and rec[-1]["expected"] == "sha256:a" and rec[-1]["actual"] == "sha256:b"


def test_emit_sandbox_degraded_should_carry_requested_and_tier(tmp_path):
    from agent_runner.api import emit_plugin_sandbox_degraded

    emit_plugin_sandbox_degraded(
        tmp_path, requested="require", achieved_tier="unconfined", reason="x"
    )

    rec = [e for e in read_events(tmp_path) if e["event"] == events.PLUGIN_SANDBOX_DEGRADED]
    assert rec and rec[-1]["requested"] == "require" and rec[-1]["achieved_tier"] == "unconfined"


def test_load_should_refuse_when_third_party_pin_mismatches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(
        PluginsConfig(pin={"fc_thirdparty": "sha256:" + "0" * 64}), log_dir=tmp_path
    )

    assert "fc_thirdparty" not in loaded_manifest_names()


def test_load_should_refuse_unpinned_third_party_when_sandbox_require(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(sandbox="require"), log_dir=tmp_path)

    assert "fc_thirdparty" not in loaded_manifest_names()


def test_load_should_admit_pinned_third_party_when_checksum_matches(monkeypatch, tmp_path):
    from agent_runner._plugin_checksum import compute_plugin_checksum

    real = compute_plugin_checksum(__name__)
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(
        PluginsConfig(pin={"fc_thirdparty": real}), log_dir=tmp_path
    )

    assert "fc_thirdparty" in loaded_manifest_names()


def test_load_should_admit_unpinned_third_party_when_sandbox_prefer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(sandbox="prefer"), log_dir=tmp_path)

    assert "fc_thirdparty" in loaded_manifest_names()


def test_load_should_admit_builtin_without_a_pin_even_under_require(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("pi", "agent_runner.builtin_plugins.pi:PLUGIN")],
    )

    agent_runner.load_and_register_plugins(PluginsConfig(sandbox="require"), log_dir=tmp_path)

    assert "pi" in loaded_manifest_names()


def test_load_should_refuse_and_signal_when_third_party_squats_a_builtin_name(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("pi", f"{__name__}:PLUGIN")])

    with pytest.warns(UserWarning, match="reserved builtin name"):
        agent_runner.load_and_register_plugins(PluginsConfig(sandbox="require"), log_dir=tmp_path)

    squat = [e for e in read_events(tmp_path) if e["event"] == events.PLUGIN_BUILTIN_NAME_SQUAT]
    assert "fc_thirdparty" not in loaded_manifest_names()
    assert squat and squat[-1]["name"] == "pi"


def test_load_should_grant_in_process_trust_only_to_genuine_builtin_dirty_handler(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        agent_runner,
        "_DISCOVERED_PLUGIN_ENTRIES",
        [("default_dirty_handler", "agent_runner.builtin_plugins.default_dirty_handler:PLUGIN")],
    )

    agent_runner.load_and_register_plugins(PluginsConfig(sandbox="require"), log_dir=tmp_path)

    handlers = [h for h in hooks._DIRTY_HANDLERS if h.name == "default_dirty_handler"]
    assert "default_dirty_handler" in loaded_manifest_names()
    assert handlers and hooks._DIRTY_HANDLER_BUILTIN[id(handlers[0])] is True


def test_load_should_emit_checksum_mismatch_event_with_the_computed_actual(monkeypatch, tmp_path):
    from agent_runner._plugin_checksum import compute_plugin_checksum

    real = compute_plugin_checksum(__name__)
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(
        PluginsConfig(pin={"fc_thirdparty": "sha256:" + "0" * 64}), log_dir=tmp_path
    )

    rec = [e for e in read_events(tmp_path) if e["event"] == events.PLUGIN_CHECKSUM_MISMATCH]
    assert rec and rec[-1]["name"] == "fc_thirdparty" and rec[-1]["actual"] == real


def test_load_should_emit_sandbox_degraded_event_when_refusing_unpinned_under_require(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("fc_thirdparty", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(sandbox="require"), log_dir=tmp_path)

    rec = [e for e in read_events(tmp_path) if e["event"] == events.PLUGIN_SANDBOX_DEGRADED]
    assert rec and rec[-1]["requested"] == "require"
