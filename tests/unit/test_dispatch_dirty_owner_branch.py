"""dispatch_dirty routes a dirty handler by GENUINE-builtin trust keyed on the
handler OBJECT identity (_DIRTY_HANDLER_BUILTIN), never the collidable owner
name: a handler registered builtin=True runs in-process; anything else --
including a handler whose owner name collides with a builtin, or the
default/legacy False -- goes through the Landlock+seccomp trampoline whenever
sandbox != 'off'. These tests stub the trampoline launcher so the routing
decision is verified without a real Linux sandbox."""

from __future__ import annotations

from agent_runner import hooks
from agent_runner._registry import BUILTIN_PLUGIN_NAMES
from tests._test_helpers import isolating, make_hook_context

_reset = isolating(hooks._DIRTY_HANDLERS, hooks._DIRTY_HANDLER_OWNER, hooks._DIRTY_HANDLER_BUILTIN)


class _Handler:
    priority = 0

    def __init__(self, name: str = "acme_dirty"):
        self.name = name
        self.calls = 0

    def handle_dirty(self, ctx, dirty_files):
        self.calls += 1
        return None


def test_register_dirty_handler_should_record_owner_when_given() -> None:
    handler = _Handler()

    hooks.register_dirty_handler(handler, owner="acme_pkg")

    assert hooks._DIRTY_HANDLER_OWNER[id(handler)] == "acme_pkg"


def test_register_dirty_handler_should_default_owner_empty_when_omitted() -> None:
    handler = _Handler()

    hooks.register_dirty_handler(handler)

    assert hooks._DIRTY_HANDLER_OWNER[id(handler)] == ""


def test_register_dirty_handler_should_default_builtin_false_when_omitted() -> None:
    handler = _Handler()

    hooks.register_dirty_handler(handler, owner="acme_pkg")

    assert hooks._DIRTY_HANDLER_BUILTIN[id(handler)] is False


def test_register_dirty_handler_should_record_builtin_trust_when_flagged() -> None:
    handler = _Handler()

    hooks.register_dirty_handler(handler, owner="pi", builtin=True)

    assert hooks._DIRTY_HANDLER_BUILTIN[id(handler)] is True


def test_dispatch_dirty_should_run_in_process_when_registered_builtin(tmp_path) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="default_dirty_handler", builtin=True)

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="require")

    assert handler.calls == 1


def test_dispatch_dirty_should_trampoline_when_builtin_name_but_registered_third_party(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    builtin_name = next(iter(BUILTIN_PLUGIN_NAMES))
    hooks.register_dirty_handler(handler, owner=builtin_name)
    routed: list[str] = []

    import agent_runner._plugin_sandbox as ps

    monkeypatch.setattr(
        ps, "run_hook_sandboxed", lambda *a, **k: routed.append("trampoline") or None
    )

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer")

    assert handler.calls == 0 and routed == ["trampoline"]


def test_dispatch_dirty_should_trampoline_third_party_when_sandbox_on(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="acme_pkg")
    seen: dict = {}

    def _fake_sandboxed(
        hook_kind, owner, hook_name, ctx, *, log_dir, dirty_files=None, timeout_s=30.0
    ):
        seen.update(hook_kind=hook_kind, owner=owner, hook_name=hook_name)
        return None

    import agent_runner._plugin_sandbox as ps

    monkeypatch.setattr(ps, "run_hook_sandboxed", _fake_sandboxed)

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer")

    assert handler.calls == 0
    assert seen == {"hook_kind": "dirty_handler", "owner": "acme_pkg", "hook_name": "acme_dirty"}


def test_dispatch_dirty_should_run_third_party_in_process_when_sandbox_off(tmp_path) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="acme_pkg")

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="off")

    assert handler.calls == 1


def test_dispatch_dirty_should_treat_empty_owner_as_third_party_when_sandbox_on(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler)  # no owner -> "" -> third-party (fail closed)
    routed: list[str] = []

    import agent_runner._plugin_sandbox as ps

    monkeypatch.setattr(
        ps, "run_hook_sandboxed", lambda *a, **k: routed.append("trampoline") or None
    )

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer")

    assert handler.calls == 0 and routed == ["trampoline"]


def test_dispatch_dirty_should_isolate_third_party_when_trampoline_raises(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="acme_pkg")

    import agent_runner._plugin_sandbox as ps

    def _boom(*a, **k):
        raise RuntimeError("trampoline killed by signal 31")

    monkeypatch.setattr(ps, "run_hook_sandboxed", _boom)

    outcome = hooks.dispatch_dirty(
        make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer"
    )

    from tests._test_helpers import read_events_for_current_month

    failed = [e for e in read_events_for_current_month(tmp_path) if e["event"] == "hook_failed"]
    assert outcome is None
    assert failed and failed[-1]["hook_name"] == "acme_dirty"


def test_dispatch_dirty_should_fail_closed_when_require_but_platform_cannot_confine(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="acme_pkg")

    import agent_runner._plugin_sandbox as ps

    monkeypatch.setattr(ps, "_sandbox_enforceable", lambda: False)

    outcome = hooks.dispatch_dirty(
        make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="require"
    )

    from tests._test_helpers import read_events_for_current_month

    failed = [e for e in read_events_for_current_month(tmp_path) if e["event"] == "hook_failed"]
    assert handler.calls == 0 and outcome is None
    assert failed and failed[-1]["hook_name"] == "acme_dirty"


def test_dispatch_dirty_should_still_attempt_trampoline_for_prefer_when_platform_cannot_confine(
    tmp_path, monkeypatch
) -> None:
    handler = _Handler()
    hooks.register_dirty_handler(handler, owner="acme_pkg")
    routed: list[str] = []

    import agent_runner._plugin_sandbox as ps

    monkeypatch.setattr(ps, "_sandbox_enforceable", lambda: False)
    monkeypatch.setattr(ps, "run_hook_sandboxed", lambda *a, **k: routed.append("launched") or None)

    hooks.dispatch_dirty(make_hook_context(tmp_path), ["f.py"], tmp_path, sandbox="prefer")

    assert routed == ["launched"] and handler.calls == 0
