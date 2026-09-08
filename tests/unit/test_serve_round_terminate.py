"""`_terminate_round`'s post-killpg wait must fail open. A D-state
(uninterruptible-sleep) leader can outlive even a killpg SIGKILL, so a bare
`proc.wait(timeout=10)` there would raise `TimeoutExpired` straight back into
`_spawn_round`'s own `except BaseException` cleanup call -- which calls
`_terminate_round` again, raises again, and escapes `cmd()` as an
unclassified exit 1 instead of a defined, classifiable returncode."""

from __future__ import annotations

import subprocess

from agent_runner.cli import _serve_round


class _WedgedProc:
    pid = 4242

    def terminate(self):
        pass

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="round", timeout=timeout)


def test_terminate_round_returns_sentinel_on_dstate_leader(monkeypatch):
    """A killpg'd-but-unreapable D-state leader must not raise TimeoutExpired out
    of _terminate_round (which would escape cmd() unclassified as exit 1)."""
    killpg_calls = []
    monkeypatch.setattr(_serve_round.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    rc = _serve_round._terminate_round(_WedgedProc())
    assert rc == _serve_round._ROUND_UNREAPED_RC  # a defined sentinel, not a raise
    assert killpg_calls == [(4242, _serve_round.signal.SIGKILL)]  # escalation still fired
