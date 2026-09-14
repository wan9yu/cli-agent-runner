"""Wire-protocol + emit tests for the plugin sandbox trampoline. Everything
here is platform-agnostic: it exercises the parent-side parse/redact/cap/schema
path and the child's pure run-helper with the restrict step stubbed out, so the
whole file runs on macOS. The real Landlock+seccomp DENIAL is asserted only on
Linux (tests/linux/test_plugin_sandbox_kill.py)."""

from __future__ import annotations

import json
import sys

import pytest

from agent_runner import events
from tests._test_helpers import make_hook_context, read_events_for_current_month


def test_emit_plugin_sandbox_kill_should_record_hook_and_signal_when_called(tmp_path) -> None:
    from agent_runner.api import emit_plugin_sandbox_kill

    emit_plugin_sandbox_kill(tmp_path, hook="acme_dirty", signal=31)

    recorded = [
        e
        for e in read_events_for_current_month(tmp_path)
        if e["event"] == events.PLUGIN_SANDBOX_KILL
    ]
    assert recorded and recorded[-1]["hook"] == "acme_dirty" and recorded[-1]["signal"] == 31


def test_parse_dirty_stdout_should_return_none_when_kind_null() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = b'{"schema": "dirty_outcome/1", "kind": null, "ref": null}'

    assert _parse_dirty_stdout(raw) is None


def test_parse_dirty_stdout_should_return_outcome_when_kind_committed() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = b'{"schema": "dirty_outcome/1", "kind": "committed", "ref": "abc123"}'

    outcome = _parse_dirty_stdout(raw)

    assert outcome.kind == "committed" and outcome.ref == "abc123"


def test_parse_dirty_stdout_should_reject_when_unknown_top_level_key() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = b'{"schema": "dirty_outcome/1", "kind": null, "ref": null, "evil": 1}'

    with pytest.raises(ValueError, match="invalid dirty_outcome"):
        _parse_dirty_stdout(raw)


def test_parse_dirty_stdout_should_reject_when_schema_tag_wrong() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = b'{"schema": "attacker/9", "kind": null, "ref": null}'

    with pytest.raises(ValueError, match="invalid dirty_outcome"):
        _parse_dirty_stdout(raw)


def test_parse_dirty_stdout_should_reject_when_not_a_json_object() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = b'["not", "an", "object"]'

    with pytest.raises(ValueError):
        _parse_dirty_stdout(raw)


def test_parse_dirty_stdout_should_reject_when_over_wire_cap() -> None:
    from agent_runner._plugin_sandbox import _MAX_WIRE_BYTES, _parse_dirty_stdout

    raw = (
        b'{"schema": "dirty_outcome/1", "kind": "committed", "ref": "'
        + b"x" * _MAX_WIRE_BYTES
        + b'"}'
    )

    with pytest.raises(ValueError, match="wire cap"):
        _parse_dirty_stdout(raw)


def test_parse_dirty_stdout_should_redact_secret_in_ref_when_present() -> None:
    from agent_runner._plugin_sandbox import _parse_dirty_stdout

    raw = (
        b'{"schema": "dirty_outcome/1", "kind": "committed", '
        b'"ref": "sk-ant-deadbeefdeadbeefdeadbeef01234567"}'
    )

    outcome = _parse_dirty_stdout(raw)

    assert "sk-ant" not in (outcome.ref or "")


def test_ctx_wire_roundtrip_should_preserve_fields_when_serialized(tmp_path) -> None:
    from agent_runner._plugin_sandbox import _ctx_from_wire, _ctx_to_wire
    from agent_runner.hooks import VcsHookView

    ctx = make_hook_context(tmp_path, agent_name="claude", round_num=7, vcs=VcsHookView("stash", 5))

    restored = _ctx_from_wire(_ctx_to_wire(ctx))

    assert restored.work_dir == ctx.work_dir
    assert restored.round_num == 7
    assert restored.vcs.dirty_action == "stash" and restored.vcs.stash_idempotency_s == 5


def _write_fake_plugin(tmp_path, *, name: str, hook_name: str, body: str) -> str:
    module_name = f"fake_plugin_{name}"
    src = (
        "from agent_runner._plugin_manifest import PluginManifest\n"
        "from agent_runner.api_types import DirtyOutcome\n\n\n"
        "class _H:\n"
        f"    name = {hook_name!r}\n"
        "    priority = 0\n\n"
        "    def handle_dirty(self, ctx, dirty_files):\n"
        f"{body}\n\n\n"
        f"PLUGIN = PluginManifest(name={name!r}, dirty_handlers=(_H(),))\n"
    )
    (tmp_path / f"{module_name}.py").write_text(src, encoding="utf-8")
    return module_name


def test_run_dirty_child_should_emit_null_outcome_when_handler_returns_none(
    tmp_path, monkeypatch
) -> None:
    module_name = _write_fake_plugin(
        tmp_path, name="noop", hook_name="noop_dirty", body="        return None"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from agent_runner import _plugin_sandbox

    result = _plugin_sandbox._run_dirty_child(
        module_name,
        "PLUGIN",
        "noop_dirty",
        make_hook_context(tmp_path),
        ["f.py"],
        restrict=lambda c: None,
    )

    assert result == {"schema": "dirty_outcome/1", "kind": None, "ref": None}


def test_run_dirty_child_should_emit_committed_outcome_when_handler_commits(
    tmp_path, monkeypatch
) -> None:
    module_name = _write_fake_plugin(
        tmp_path,
        name="commit",
        hook_name="commit_dirty",
        body='        return DirtyOutcome(kind="committed", ref="deadbeef")',
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from agent_runner import _plugin_sandbox

    result = _plugin_sandbox._run_dirty_child(
        module_name,
        "PLUGIN",
        "commit_dirty",
        make_hook_context(tmp_path),
        ["f.py"],
        restrict=lambda c: None,
    )

    assert result == {"schema": "dirty_outcome/1", "kind": "committed", "ref": "deadbeef"}


def test_run_dirty_child_should_apply_restrict_before_importing_plugin(
    tmp_path, monkeypatch
) -> None:
    module_name = _write_fake_plugin(
        tmp_path, name="order", hook_name="order_dirty", body="        return None"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from agent_runner import _plugin_sandbox

    calls: list[str] = []
    monkeypatch.setitem(sys.modules, module_name, None)  # ensure not pre-imported
    sys.modules.pop(module_name, None)

    def _restrict(ctx) -> None:
        calls.append("restrict")
        assert module_name not in sys.modules, "plugin imported before confinement was applied"

    _plugin_sandbox._run_dirty_child(
        module_name, "PLUGIN", "order_dirty", make_hook_context(tmp_path), [], restrict=_restrict
    )

    assert calls == ["restrict"] and module_name in sys.modules


def test_trampoline_subprocess_should_roundtrip_outcome_when_run_end_to_end(
    tmp_path, monkeypatch
) -> None:
    module_name = _write_fake_plugin(
        tmp_path,
        name="e2e",
        hook_name="e2e_dirty",
        body='        return DirtyOutcome(kind="ignored", ref=None)',
    )
    import os
    import subprocess

    ctx = make_hook_context(tmp_path)
    payload = {
        "schema": "plugin_sandbox_input/1",
        "ctx": {
            "work_dir": str(ctx.work_dir),
            "log_dir": str(ctx.log_dir),
            "project": ctx.project,
            "round_num": ctx.round_num,
            "phase": ctx.phase,
            "agent_name": ctx.agent_name,
            "agent_binary": ctx.agent_binary,
            "agent_log_path": str(ctx.agent_log_path),
            "dry_run": ctx.dry_run,
            "anomaly_repetitive_window": ctx.anomaly_repetitive_window,
            "anomaly_repetitive_threshold": ctx.anomaly_repetitive_threshold,
            "vcs": None,
        },
        "hook_kind": "dirty_handler",
        "spawn_view": None,
        "dirty_files": ["f.py"],
    }
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner._plugin_sandbox",
            "dirty_handler",
            module_name,
            "PLUGIN",
            "e2e_dirty",
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert json.loads(proc.stdout) == {"schema": "dirty_outcome/1", "kind": "ignored", "ref": None}


def test_child_env_should_drop_parent_secrets_but_keep_git_and_path(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("ACME_TOKEN", "t0ken")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tester")

    from agent_runner._plugin_sandbox import _child_env

    env = _child_env()

    assert "ANTHROPIC_API_KEY" not in env and "ACME_TOKEN" not in env
    assert env["PATH"] == "/usr/bin" and env["GIT_AUTHOR_NAME"] == "tester"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"


def test_run_hook_sandboxed_should_hide_parent_secrets_from_child(tmp_path, monkeypatch) -> None:
    body = (
        "        import os\n"
        '        return DirtyOutcome(kind="committed", '
        'ref=os.environ.get("ANTHROPIC_API_KEY") or "ABSENT")'
    )
    module_name = _write_fake_plugin(tmp_path, name="leak", hook_name="leak_dirty", body=body)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    from agent_runner._plugin_sandbox import run_hook_sandboxed

    outcome = run_hook_sandboxed(
        "dirty_handler",
        module_name,
        "PLUGIN",
        "leak_dirty",
        make_hook_context(tmp_path),
        log_dir=tmp_path,
        dirty_files=["f.py"],
    )

    assert outcome.kind == "committed"
    assert outcome.ref == "ABSENT"


def test_run_hook_sandboxed_should_bound_child_output_when_plugin_floods_stdout(
    tmp_path, monkeypatch
) -> None:
    body = "        import os\n        os.write(1, b'x' * (128 * 1024))\n        return None"
    module_name = _write_fake_plugin(tmp_path, name="flood", hook_name="flood_dirty", body=body)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    from agent_runner._plugin_sandbox import run_hook_sandboxed

    with pytest.raises((ValueError, RuntimeError)):
        run_hook_sandboxed(
            "dirty_handler",
            module_name,
            "PLUGIN",
            "flood_dirty",
            make_hook_context(tmp_path),
            log_dir=tmp_path,
            dirty_files=[],
        )
