from __future__ import annotations

from unittest.mock import patch

from agent_runner import api
from agent_runner.api_types import Alert


class _StopError(Exception):
    """Breaks the monitor loop from the fake sleep. NOT StopIteration — raising
    StopIteration inside a generator becomes RuntimeError (PEP 479)."""


def _disk(v: float) -> Alert:
    return Alert("warning", "disk_warning", "m", {"value": v, "threshold": 90.0}, "t")


def test_recovered_then_recurred_alert_fires_twice(tmp_path, monkeypatch) -> None:
    # poll 1: firing; poll 2: cleared (empty); poll 3: firing again -> should re-yield.
    polls = iter([[_disk(91.0)], [], [_disk(92.0)]])
    monkeypatch.setattr(api, "_poll_once", lambda _wd, **_kwargs: next(polls))
    sleeps = {"n": 0}

    def _fake_sleep(_s) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 3:  # let all three polls run; break on the 3rd sleep
            raise _StopError

    monkeypatch.setattr(api.SYSTEM_CLOCK, "sleep", _fake_sleep)
    mon = type("M", (), {"auto_stop_on": ()})()
    cfg = type("C", (), {"runtime": type("R", (), {"log_dir": tmp_path})(), "monitor": mon})()
    monkeypatch.setattr(api, "load_config", lambda _p: cfg)
    tmp_path.mkdir(exist_ok=True)
    with patch.object(api.monitor, "on_alert"):
        gen = api._monitor_loop_iter(tmp_path)
        seen = []
        try:
            for a in gen:
                seen.append(a.context["value"])
        except _StopError:
            pass
    assert seen == [91.0, 92.0]  # not deduped away — the episode ended and re-armed


def _oauth_fail() -> Alert:
    return Alert("critical", "oauth_fail", "m", {}, "t", auto_action="stop_service")


def _run_loop_over_persisting_alert(tmp_path, monkeypatch, *, on_alert_returns: str) -> int:
    """Feed the SAME alert (never clearing) to three consecutive polls, with
    ``monitor.on_alert`` stubbed to always return ``on_alert_returns``. Returns
    how many times on_alert was actually called -- the thing that differs
    between a "draining" verdict (must re-fire every poll) and every other
    verdict (must stay suppressed by the normal dedup)."""
    alert = _oauth_fail()
    polls = iter([[alert], [alert], [alert]])
    monkeypatch.setattr(api, "_poll_once", lambda _wd, **_kwargs: next(polls))
    sleeps = {"n": 0}

    def _fake_sleep(_s) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 3:  # let all three polls run
            raise _StopError

    monkeypatch.setattr(api.SYSTEM_CLOCK, "sleep", _fake_sleep)
    mon = type("M", (), {"auto_stop_on": ()})()
    cfg = type("C", (), {"runtime": type("R", (), {"log_dir": tmp_path})(), "monitor": mon})()
    monkeypatch.setattr(api, "load_config", lambda _p: cfg)
    tmp_path.mkdir(exist_ok=True)
    calls = {"n": 0}

    def _fake_on_alert(*_a, **_k) -> str:
        calls["n"] += 1
        return on_alert_returns

    with patch.object(api.monitor, "on_alert", side_effect=_fake_on_alert):
        gen = api._monitor_loop_iter(tmp_path)
        try:
            for _ in gen:
                pass
        except _StopError:
            pass
    return calls["n"]


def test_draining_verdict_forces_rearm_so_persisting_alert_refires_every_poll(
    tmp_path, monkeypatch
) -> None:
    """A "draining" verdict means on_alert recorded NOTHING for this alert this
    poll (see on_alert's docstring) -- the caller must force its `seen` entry
    to re-arm so the identical, still-persisting alert is handed to on_alert
    again on the very next poll, instead of sitting suppressed under the
    normal dedup with no outcome ever recorded."""
    calls = _run_loop_over_persisting_alert(tmp_path, monkeypatch, on_alert_returns="draining")
    assert calls == 3  # every poll re-fired -- never suppressed


def test_non_draining_verdict_keeps_normal_dedup_suppression(tmp_path, monkeypatch) -> None:
    """Contrast case: "failed" (like "triggered"/"none") recorded a real,
    final outcome for this episode, so the persisting alert must stay
    suppressed by the normal dedup -- re-arming here would re-run the stop
    attempt (and re-emit) every single poll instead of once per episode."""
    calls = _run_loop_over_persisting_alert(tmp_path, monkeypatch, on_alert_returns="failed")
    assert calls == 1  # suppressed on polls 2 and 3
