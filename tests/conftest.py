"""Shared test fixtures."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_plugin_registries():
    """Snapshot, clear, and restore every process-global plugin registry around
    each test.

    Plugin registration now happens at ``load_config`` time, not package import,
    so ``import agent_runner`` leaves these registries EMPTY. A test that
    registers a hook/handler/detector (directly, or by calling ``load_config``)
    would otherwise LEAK it into later tests -- an order- and worker-dependent
    flake. This resets every test to that real baseline (empty unless the test
    itself registers) and contains any registration it makes.

    Containers are copied at the container level (a fresh list/dict holding the
    SAME element references) -- never element-deep-copied: the owner/builtin/
    module dicts are keyed on ``id(handler)``, so cloning the handler objects
    would change their identities and break the keying.
    """
    from agent_runner import _plugin_manifest, events, hooks, monitor

    registries: list = [
        hooks._DIRTY_HANDLERS,
        hooks._SPAWN_HOOKS,
        hooks._PRE_ROUND_HOOKS,
        hooks._CONTEXT_ENRICHERS,
        hooks._POST_ROUND_HOOKS,
        hooks._SERVE_STARTUP_HOOKS,
        monitor._PLUGIN_DETECTORS,
        hooks._DIRTY_HANDLER_OWNER,
        hooks._SPAWN_HOOK_OWNER,
        hooks._DIRTY_HANDLER_BUILTIN,
        hooks._SPAWN_HOOK_BUILTIN,
        hooks._DIRTY_HANDLER_MODULE,
        hooks._SPAWN_HOOK_MODULE,
        _plugin_manifest._LOADED_MANIFESTS,
        events._PLUGIN_KINDS,
    ]
    saved = [reg.copy() if isinstance(reg, dict) else list(reg) for reg in registries]
    for reg in registries:
        reg.clear()
    try:
        yield
    finally:
        for reg, snap in zip(registries, saved, strict=True):
            reg.clear()
            if isinstance(reg, dict):
                reg.update(snap)
            else:
                reg.extend(snap)


@pytest.fixture(autouse=True)
def _isolate_serve_doorbell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ``serve_cmd.open_listener`` to a ``NullListener`` for the whole
    suite.

    ``serve_cmd.cmd()`` now opens a real doorbell ``Listener`` (mkfifo + fd)
    and registers it with the process-global ``signal.set_wakeup_fd`` on every
    call (see ``_open_serve_doorbell``). A test that drives ``cmd()`` end to
    end without stubbing this would otherwise leak a FIFO file + fd per test
    and re-clobber the one process-wide wakeup-fd registration -- unbounded
    across the whole suite, not just within one test's own teardown.

    A test that wants the REAL doorbell mechanism under test builds its own
    ``Listener`` directly (see ``test_serve_wakeup.py``) rather than going
    through ``serve_cmd.open_listener`` at all, so this default never shadows
    it; a test can still opt back into a real listener for `cmd()` itself with
    its own ``monkeypatch.setattr(serve_cmd, "open_listener", ...)`` after this
    fixture runs (last-write-wins, standard monkeypatch layering)."""
    from agent_runner._notify import NullListener
    from agent_runner.cli import serve_cmd

    monkeypatch.setattr(serve_cmd, "open_listener", lambda log_dir: NullListener())


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a real git repo in tmp_path (commits enabled)."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def fake_agent_script(tmp_path: Path) -> Path:
    """A 5-line bash script controlled by FAKE_AGENT_BEHAVIOR env var."""
    script = tmp_path / "fake-agent.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "fake agent starting" >&2\n'
        'case "${FAKE_AGENT_BEHAVIOR:-succeed}" in\n'
        "  succeed) exit 0 ;;\n"
        '  dirty)   echo x > "$WORK_DIR/dirty.txt"; exit 0 ;;\n'
        "  hang)    sleep 9999 ;;\n"
        "  crash)   exit 137 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def e2e_pi_enabled() -> bool:
    return bool(os.getenv("AGENT_RUNNER_E2E_PI"))


@pytest.fixture
def in_repo_root() -> Iterator[Path]:
    """For invariant tests that must scan the real codebase."""
    yield Path(__file__).resolve().parent.parent


@pytest.fixture
def minimal_config(tmp_path: Path):
    """Returns a Config with a neutral 'fake-agent' command, suitable for unit
    tests that don't care which CLI is invoked. Use when you need a Config but
    the specific [agent].command value is irrelevant."""
    from agent_runner.config import (
        AgentConfig,
        Config,
        PromptConfig,
        RuntimeConfig,
        VcsConfig,
    )

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test prompt\n")
    return Config(
        agent=AgentConfig(
            command=["fake-agent"],
            prompt_arg_template=["-p", "{prompt}"],
        ),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=prompt_file, inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )
