from __future__ import annotations

import pytest

from agent_runner import hooks
from agent_runner._plugin_sandbox import _parse_spawn_stdout
from agent_runner.api_types import SpawnDecision
from agent_runner.cli import _serve_round
from agent_runner.config.models import PluginsConfig
from tests._clock import FakeClock
from tests._test_helpers import isolating, make_cfg, read_events_for_current_month

_reset = isolating(hooks._SPAWN_HOOKS, hooks._SPAWN_HOOK_OWNER, hooks._SPAWN_HOOK_BUILTIN)


def _register(name, decision, *, builtin=False, owner="acme"):
    """Register a spawn hook whose in-process ``before_spawn`` returns ``decision``.
    A third-party hook is trampolined by the seam (``before_spawn`` unused — tests
    monkeypatch ``run_hook_sandboxed`` for that path); a builtin runs in-process."""
    gate = type("Gate", (), {"name": name, "before_spawn": lambda self, ctx, view: decision})()
    hooks.register_spawn_hook(gate, owner=owner, builtin=builtin)
    return gate


def _events(log_dir, kind):
    return [e for e in read_events_for_current_month(log_dir) if e["event"] == kind]


def test_parse_spawn_stdout_should_build_defer_decision_when_valid():
    raw = b'{"schema": "spawn_decision/1", "action": "defer", "defer_s": 30, "reason": "window"}'

    decision = _parse_spawn_stdout(raw)

    assert decision.action == "defer" and decision.defer_s == 30


def test_parse_spawn_stdout_should_build_proceed_when_minimal():
    raw = b'{"schema": "spawn_decision/1", "action": "proceed", "defer_s": 0, "reason": ""}'

    decision = _parse_spawn_stdout(raw)

    assert decision.action == "proceed"


def test_parse_spawn_stdout_should_reject_when_unknown_top_level_key():
    raw = b'{"schema": "spawn_decision/1", "action": "skip", "defer_s": 0, "reason": "", "evil": 1}'

    with pytest.raises(ValueError, match="invalid spawn_decision"):
        _parse_spawn_stdout(raw)


def test_parse_spawn_stdout_should_reject_when_schema_tag_wrong():
    raw = b'{"schema": "dirty_outcome/1", "action": "skip", "defer_s": 0, "reason": ""}'

    with pytest.raises(ValueError, match="invalid spawn_decision"):
        _parse_spawn_stdout(raw)


def test_parse_spawn_stdout_should_reject_when_action_out_of_vocabulary():
    raw = b'{"schema": "spawn_decision/1", "action": "launch", "defer_s": 0, "reason": ""}'

    with pytest.raises(ValueError, match="invalid spawn_decision action"):
        _parse_spawn_stdout(raw)


def test_run_spawn_child_should_serialize_proceed_when_hook_returns_none(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    from agent_runner import _plugin_sandbox
    from tests._test_helpers import make_hook_context

    class _Hook:
        name = "gate"

        def before_spawn(self, ctx, view):
            return None

    target = SimpleNamespace(spawn_hooks=(_Hook(),))
    monkeypatch.setattr(importlib, "import_module", lambda _m: target)
    view = _plugin_sandbox.hooks.SpawnView(argv=("true",), env={"PATH": ""})

    result = _plugin_sandbox._run_spawn_child(
        "m", "", "gate", make_hook_context(tmp_path), view, restrict=lambda _ctx: None
    )

    assert result == {"schema": "spawn_decision/1", "action": "proceed", "defer_s": 0, "reason": ""}


def test_run_hook_sandboxed_should_send_env_names_only_when_spawn_view_given(tmp_path, monkeypatch):
    import json

    from agent_runner import _plugin_sandbox
    from tests._test_helpers import make_hook_context

    captured: dict = {}

    def _fake_child(argv, stdin_bytes, timeout_s, *, wake_fd=None, should_stop=None):
        captured["payload"] = json.loads(stdin_bytes.decode("utf-8"))
        return (
            0,
            b'{"schema": "spawn_decision/1", "action": "proceed", "defer_s": 0, "reason": ""}',
            b"",
        )

    monkeypatch.setattr(_plugin_sandbox, "_run_child_process", _fake_child)
    view = _plugin_sandbox.hooks.SpawnView(
        argv=("docker", "run", "img"), env={"ANTHROPIC_API_KEY": "s3cr3t", "PATH": "/usr/bin"}
    )

    _plugin_sandbox.run_hook_sandboxed(
        "spawn_hook", "m", "a", "gate", make_hook_context(tmp_path), log_dir=tmp_path, view=view
    )

    wire = captured["payload"]["spawn_view"]
    assert wire["argv"] == ["docker", "run", "img"]
    assert sorted(wire["env_names"]) == ["ANTHROPIC_API_KEY", "PATH"]
    assert "s3cr3t" not in json.dumps(captured["payload"])  # no secret VALUE crosses the wire


def test_seam_should_return_false_when_no_spawn_hooks_registered(tmp_path):
    cfg = make_cfg(tmp_path, plugins=PluginsConfig())

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg, tmp_path, {"requested": False}, phase=None, work_dir=tmp_path, engaged=True
    )

    assert consumed is False


def test_seam_should_ignore_override_when_hook_not_allow_listed(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("skip", reason="no"))
    monkeypatch.setattr(
        _serve_round, "run_hook_sandboxed", lambda *a, **k: SpawnDecision("skip", reason="no")
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=[]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg, tmp_path, {"requested": False}, phase=None, work_dir=tmp_path, engaged=True
    )

    ignored = _events(tmp_path, "plugin_spawn_override_ignored")
    assert consumed is False
    assert ignored and ignored[-1]["hook"] == "gate_a"


def test_seam_should_skip_when_hook_allow_listed(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("skip", reason="halt"))
    monkeypatch.setattr(
        _serve_round, "run_hook_sandboxed", lambda *a, **k: SpawnDecision("skip", reason="halt")
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_a"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=True,
        clock=FakeClock(),
    )

    decision = _events(tmp_path, "plugin_spawn_decision")
    assert consumed is True
    assert decision[-1]["action"] == "skip" and decision[-1]["hook"] == "gate_a"
    assert not _events(tmp_path, "plugin_spawn_override_ignored")


def test_seam_should_defer_when_hook_allow_listed(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("defer", defer_s=5, reason="window"))
    monkeypatch.setattr(
        _serve_round,
        "run_hook_sandboxed",
        lambda *a, **k: SpawnDecision("defer", defer_s=5, reason="window"),
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_a"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=True,
        clock=FakeClock(),
    )

    assert consumed is True
    assert _events(tmp_path, "round_deferred") and _events(tmp_path, "round_resumed")
    decision = _events(tmp_path, "plugin_spawn_decision")
    assert decision[-1]["action"] == "defer" and decision[-1]["defer_s"] == 5


def test_seam_should_proceed_when_hook_returns_proceed(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("proceed"))
    monkeypatch.setattr(
        _serve_round, "run_hook_sandboxed", lambda *a, **k: SpawnDecision("proceed")
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_a"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg, tmp_path, {"requested": False}, phase=None, work_dir=tmp_path, engaged=True
    )

    assert consumed is False
    assert not _events(tmp_path, "plugin_spawn_decision")


def test_seam_should_isolate_failing_hook_as_proceed(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("skip"))

    def _boom(*a, **k):
        raise RuntimeError("trampoline exploded")

    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", _boom)
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_a"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg, tmp_path, {"requested": False}, phase=None, work_dir=tmp_path, engaged=True
    )

    failures = _events(tmp_path, "hook_failed")
    assert consumed is False
    assert failures and failures[-1]["hook_kind"] == "spawn_hook"


def test_seam_should_run_builtin_hook_in_process_not_trampoline(tmp_path, monkeypatch):
    _register("gate_b", SpawnDecision("skip", reason="builtin-halt"), builtin=True)

    def _must_not_run(*a, **k):
        raise AssertionError("a genuine builtin must NOT be trampolined")

    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", _must_not_run)
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_b"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=True,
        clock=FakeClock(),
    )

    assert consumed is True
    assert _events(tmp_path, "plugin_spawn_decision")[-1]["action"] == "skip"


def test_seam_should_collapse_skip_over_defer_across_hooks(tmp_path, monkeypatch):
    _register("gate_defer", SpawnDecision("defer", defer_s=9), owner="acme")
    _register("gate_skip", SpawnDecision("skip", reason="stop"), owner="acme")
    decisions = {
        "gate_defer": SpawnDecision("defer", defer_s=9),
        "gate_skip": SpawnDecision("skip", reason="stop"),
    }
    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", lambda *a, **k: decisions[a[3]])
    cfg = make_cfg(
        tmp_path, plugins=PluginsConfig(spawn_override_allow=["gate_defer", "gate_skip"])
    )

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=True,
        clock=FakeClock(),
    )

    decision = _events(tmp_path, "plugin_spawn_decision")
    assert consumed is True
    assert decision[-1]["action"] == "skip" and decision[-1]["hook"] == "gate_skip"


def test_seam_should_run_third_party_hook_in_process_when_sandbox_off(tmp_path, monkeypatch):
    seen = []
    gate = type(
        "Gate",
        (),
        {
            "name": "gate_a",
            "before_spawn": lambda self, ctx, view: (
                seen.append(view) or SpawnDecision("skip", reason="halt")
            ),
        },
    )()
    hooks.register_spawn_hook(gate, owner="acme", builtin=False)

    def _must_not_trampoline(*a, **k):
        raise AssertionError("sandbox=off must run a third-party hook in-process, not trampoline")

    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", _must_not_trampoline)
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="off", spawn_override_allow=["gate_a"]))

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=False,
        clock=FakeClock(),
    )

    decision = _events(tmp_path, "plugin_spawn_decision")
    assert consumed is True
    assert seen and all(value == "" for value in seen[0].env.values())
    assert decision[-1]["action"] == "skip" and decision[-1]["hook"] == "gate_a"


def test_seam_should_trampoline_third_party_hook_when_sandbox_require(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("proceed"))
    routed = []
    monkeypatch.setattr(
        _serve_round,
        "run_hook_sandboxed",
        lambda *a, **k: routed.append(a[0]) or SpawnDecision("skip", reason="halt"),
    )
    cfg = make_cfg(
        tmp_path, plugins=PluginsConfig(sandbox="require", spawn_override_allow=["gate_a"])
    )

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=True,
        clock=FakeClock(),
    )

    assert consumed is True
    assert routed == ["spawn_hook"]


def test_seam_should_run_third_party_hook_in_process_when_prefer_but_not_engaged(
    tmp_path, monkeypatch
):
    seen = []
    gate = type(
        "Gate",
        (),
        {
            "name": "gate_a",
            "before_spawn": lambda self, ctx, view: (
                seen.append(1) or SpawnDecision("skip", reason="halt")
            ),
        },
    )()
    hooks.register_spawn_hook(gate, owner="acme", builtin=False)

    def _must_not_trampoline(*a, **k):
        raise AssertionError("prefer + not-engaged must run a third-party hook in-process")

    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", _must_not_trampoline)
    cfg = make_cfg(
        tmp_path, plugins=PluginsConfig(sandbox="prefer", spawn_override_allow=["gate_a"])
    )

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg,
        tmp_path,
        {"requested": False},
        phase=None,
        work_dir=tmp_path,
        engaged=False,
        clock=FakeClock(),
    )

    assert consumed is True
    assert seen == [1]
    assert not _events(tmp_path, "hook_failed")
    assert _events(tmp_path, "plugin_spawn_decision")[-1]["action"] == "skip"


def test_seam_should_refuse_third_party_hook_when_require_but_not_engaged(tmp_path, monkeypatch):
    _register("gate_a", SpawnDecision("skip", reason="halt"))

    def _must_not_trampoline(*a, **k):
        raise AssertionError("require + not-engaged must refuse, never trampoline")

    monkeypatch.setattr(_serve_round, "run_hook_sandboxed", _must_not_trampoline)
    cfg = make_cfg(
        tmp_path, plugins=PluginsConfig(sandbox="require", spawn_override_allow=["gate_a"])
    )

    consumed = _serve_round._maybe_defer_for_spawn_hooks(
        cfg, tmp_path, {"requested": False}, phase=None, work_dir=tmp_path, engaged=False
    )

    failures = _events(tmp_path, "hook_failed")
    assert consumed is False
    assert failures and failures[-1]["hook_kind"] == "spawn_hook"
