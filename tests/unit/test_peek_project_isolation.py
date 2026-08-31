"""peek must not mix a second project's service state into project A's snapshot."""

from __future__ import annotations

import os

import pytest

from tests._test_helpers import make_toml


def test_peek_uses_cwd_project_service_not_a_named_sibling(tmp_path, monkeypatch) -> None:
    from agent_runner import api

    # Project A: real toml at cwd, no serve.pid -> service inactive.
    proj_a = tmp_path / "projA"
    proj_a.mkdir()
    make_toml(proj_a)
    monkeypatch.chdir(proj_a)

    # A live serve.pid for a *different* project name, under the convention dir.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    b_logs = home / ".agent-runner" / "projB" / "logs"
    b_logs.mkdir(parents=True)
    (b_logs / "serve.pid").write_text(str(os.getpid()))

    state = api.peek("projB")
    # Bug: peek reads A's events (cwd) but B's service -> active from B's pid.
    # Fixed: service is resolved from the SAME project peek read events from (A).
    assert state.service.active is False


def test_named_project_resolution_validates_charset() -> None:
    from agent_runner import api

    with pytest.raises(ValueError):
        api.status("bad;name")
