"""B1: the round CLI installs a SIGTERM handler that raises so agent_runtime.run's
reap path fires, and cmd exits 130 on interrupt (serve must not read an interrupted
round as a completed one)."""

from __future__ import annotations

import signal
from types import SimpleNamespace

import pytest

from agent_runner.cli import round_cmd
from tests._test_helpers import make_toml


def test_install_term_handler_raises_keyboardinterrupt(monkeypatch):
    captured = {}
    monkeypatch.setattr(signal, "signal", lambda s, h: captured.__setitem__(s, h))
    round_cmd._install_term_handler()
    with pytest.raises(KeyboardInterrupt):
        captured[signal.SIGTERM](signal.SIGTERM, None)


def test_cmd_returns_130_on_keyboardinterrupt(monkeypatch, tmp_path):
    cfg_path = make_toml(tmp_path)
    monkeypatch.setattr(round_cmd, "_install_term_handler", lambda: None)

    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(round_cmd, "run_one_round", boom)
    rc = round_cmd.cmd(SimpleNamespace(config=cfg_path, phase=None))
    assert rc == 130
