"""Real Landlock+seccomp DENIAL, asserted end-to-end. seccomp is the PRIMARY
denier here: a confined trampoline child that reaches for a network syscall is
KILLED by SIGSYS (signal 31) before its handler can return.

Enforcement is Linux-only and needs the opt-in ``[sandbox]`` extra installed
(``pip install -e .[dev,sandbox]``). Where confinement cannot engage (non-Linux,
or Linux without the bindings) these are strict-xfail -- they still COLLECT and
RUN, but the asserted denial is expected to fail there rather than silently
skip-as-pass. On a capable runner the xfail condition is False, so they must
actually pass -- pinned by tests/invariants/test_kill_tests_not_skipped.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_runner._plugin_sandbox import _ctx_to_wire
from tests._test_helpers import install_hostile_dirty_plugin, make_hook_context


def _sandbox_bindings_available() -> bool:
    if sys.platform != "linux":
        return False
    try:
        import py_landlock  # noqa: F401
        import pyseccomp  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.xfail(
    not _sandbox_bindings_available(),
    reason=(
        "Landlock+seccomp trampoline confinement is enforceable only on Linux with "
        "the [sandbox] extra installed; the asserted denial cannot run here"
    ),
    strict=True,
    raises=AssertionError,
)

_SIGSYS = 31  # seccomp KILL_PROCESS delivers SIGSYS


def _run_trampoline_child(module_name, hook_name, tmp_path):
    payload = {
        "schema": "plugin_sandbox_input/1",
        "ctx": _ctx_to_wire(make_hook_context(tmp_path)),
        "hook_kind": "dirty_handler",
        "spawn_view": None,
        "dirty_files": ["f.py"],
    }
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner._plugin_sandbox",
            "dirty_handler",
            module_name,
            "PLUGIN",
            hook_name,
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_dirty_trampoline_should_die_by_sigsys_when_handler_opens_socket(tmp_path) -> None:
    module_name, hook_name = install_hostile_dirty_plugin(tmp_path, action="socket")

    result = _run_trampoline_child(module_name, hook_name, tmp_path)

    assert result.returncode < 0, result.stderr.decode(errors="replace")
    assert -result.returncode == _SIGSYS


def test_dirty_trampoline_should_die_by_signal_when_handler_connects(tmp_path) -> None:
    module_name, hook_name = install_hostile_dirty_plugin(tmp_path, action="connect")

    result = _run_trampoline_child(module_name, hook_name, tmp_path)

    assert result.returncode < 0, result.stderr.decode(errors="replace")
