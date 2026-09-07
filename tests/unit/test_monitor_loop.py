"""Failure-injection coverage for `_monitor_loop_iter`'s two fail-open guards
(F1b): a raise from `monitor.on_alert` must not end the generator, and a raise
from the startup `MONITOR_STARTED` emit must not abort the loop before it even
starts polling. Both are the supervisor's own failure domain -- the loop must
survive them, not just call through them."""

from __future__ import annotations

import itertools

from agent_runner import api, monitor
from agent_runner.api_types import Alert


class _StopError(Exception):
    """Breaks the monitor loop from the fake sleep. NOT StopIteration -- raising
    StopIteration inside a generator becomes RuntimeError (PEP 479)."""


def _oauth_fail() -> Alert:
    return Alert("critical", "oauth_fail", "x", {}, "t", auto_action="stop_service")


def _fake_cfg(tmp_path):
    mon = type("M", (), {"auto_stop_on": ()})()
    return type("C", (), {"runtime": type("R", (), {"log_dir": tmp_path})(), "monitor": mon})()


def test_loop_survives_on_alert_raise(tmp_path, monkeypatch) -> None:
    """A raise inside on_alert (its own failure domain) must not end
    supervision: the loop warns and continues polling instead of dying.

    The same persisting alert is fed every poll, so the normal dedup (verdict
    "failed", not "draining") suppresses on_alert after the first attempt --
    exactly like test_non_draining_verdict_keeps_normal_dedup_suppression.
    That makes `polls["n"] >= 2` (the loop reached a SECOND `_poll_once` call)
    the real non-vacuous survival signal here, not a second on_alert call:
    it proves the generator kept iterating past the raise instead of the
    exception propagating out and ending it.
    """
    alert = _oauth_fail()
    polls = {"n": 0}

    def fake_poll(*a, **k):
        polls["n"] += 1
        return [alert]

    monkeypatch.setattr(api, "_poll_once", fake_poll)

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("on_alert exploded")

    monkeypatch.setattr(monitor, "on_alert", boom)

    sleeps = {"n": 0}

    def stop_after_two(_s):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopError

    monkeypatch.setattr(api.SYSTEM_CLOCK, "sleep", stop_after_two)
    monkeypatch.setattr(api, "load_config", lambda _p: _fake_cfg(tmp_path))
    tmp_path.mkdir(exist_ok=True)

    gen = api._monitor_loop_iter(tmp_path, interval_s=0)
    try:
        list(itertools.islice(gen, 5))
    except _StopError:
        pass

    assert calls["n"] >= 1  # the exception path was actually exercised
    # Non-vacuous: the loop reached a second poll cycle -- proof the raise
    # inside on_alert did not end the generator.
    assert polls["n"] >= 2


def test_monitor_started_emit_failure_does_not_crash_startup(tmp_path, monkeypatch) -> None:
    """A poisoned MONITOR_STARTED breadcrumb write (e.g. ENOSPC) must not
    prevent the just-(re)started monitor from reaching its poll loop."""

    def poisoned_emit(*a, **k):
        raise OSError("ENOSPC")

    monkeypatch.setattr(api.events, "emit", poisoned_emit)
    monkeypatch.setattr(api, "_poll_once", lambda *a, **k: [])

    sleeps = {"n": 0}

    def stop_after_one(_s):
        sleeps["n"] += 1
        raise _StopError

    monkeypatch.setattr(api.SYSTEM_CLOCK, "sleep", stop_after_one)
    monkeypatch.setattr(api, "load_config", lambda _p: _fake_cfg(tmp_path))
    tmp_path.mkdir(exist_ok=True)

    gen = api._monitor_loop_iter(tmp_path, interval_s=0)
    try:
        list(itertools.islice(gen, 5))
    except _StopError:
        pass

    # Non-vacuous: the loop reached its first sleep -- proof startup survived
    # the poisoned emit and the poll loop actually began, not just that the
    # generator object was constructed (generators don't run until iterated).
    assert sleeps["n"] >= 1
